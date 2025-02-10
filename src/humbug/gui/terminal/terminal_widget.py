"""Terminal widget implementation."""

from dataclasses import dataclass
import fcntl
from typing import Optional, Tuple
import re
import logging
from enum import IntEnum
import struct
import termios

from PySide6.QtWidgets import QPlainTextEdit, QWidget
from PySide6.QtCore import Signal, Qt, QTimer, QRect
from PySide6.QtGui import (
    QTextCursor, QKeyEvent, QFont, QTextCharFormat, QMouseEvent,
    QTextFormat, QResizeEvent, QFontMetricsF, QFocusEvent, QPainter, QPaintEvent
)

from humbug.gui.color_role import ColorRole
from humbug.gui.style_manager import StyleManager


class FormatProperty(IntEnum):
    """Properties used to track which format attributes are explicit vs default."""
    CUSTOM_FOREGROUND = QTextFormat.UserProperty
    CUSTOM_BACKGROUND = QTextFormat.UserProperty + 1
    CUSTOM_WEIGHT = QTextFormat.UserProperty + 2
    CUSTOM_ITALIC = QTextFormat.UserProperty + 3
    CUSTOM_UNDERLINE = QTextFormat.UserProperty + 4


@dataclass
class TerminalSize:
    """Terminal size in rows and columns."""
    rows: int
    cols: int

    def __eq__(self, other) -> bool:
        if not isinstance(other, TerminalSize):
            return False
        return self.rows == other.rows and self.cols == other.cols

    def to_struct(self) -> bytes:
        """
        Convert terminal size to struct format for TIOCSWINSZ.

        Returns:
            bytes: Packed struct in format suitable for TIOCSWINSZ ioctl
        """
        return struct.pack('HHHH', self.rows, self.cols, 0, 0)


class TerminalWidget(QPlainTextEdit):
    """Terminal display widget with fixed-width line handling."""

    # Signal emitted when user input is ready
    data_ready = Signal(bytes)
    # Signal emitted for mouse events when tracking is enabled
    mouse_event = Signal(str)
    size_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        """Initialize terminal widget."""
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

        self._logger = logging.getLogger("TerminalWidget")
        self._style_manager = StyleManager()

        # Set up default text format
        self._default_text_format = QTextCharFormat()
        self._update_default_format()

        self._cursor_row = 0  # 0-based row in terminal
        self._cursor_col = 0  # 0-based column in terminal
        self._saved_cursor_position = None  # (row, col) tuple when saved
        self._cursor_visible = False
        self._cursor_blink_timer = QTimer(self)
        self._cursor_blink_timer.timeout.connect(self._blink_cursor)

        # Hide Qt's cursor since we'll draw our own
        self.setCursorWidth(0)

        # Current text format (initialized to default)
        self._current_text_format = self._default_text_format

        # Set up default appearance
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {self._style_manager.get_color_str(ColorRole.TAB_BACKGROUND_ACTIVE)};
                color: {self._style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
                border: none;
            }}
        """)

        # Configure cursor
        self.setCursorWidth(8)

        # ANSI escape sequence handling
        self._escape_seq_buffer = ""
        self._in_escape_seq = False
        self._saved_cursor_position = None

        # Additional terminal state
        self._alternate_screen_buffer = ""
        self._main_screen_buffer = ""
        self._main_screen_formats = []
        self._using_alternate_screen = False
        self._scroll_region: Optional[Tuple[int, int]] = None
        self._application_cursor_keys = False
        self._application_keypad_mode = False
        self._mouse_tracking = False
        self._mouse_tracking_sgr = False
        self._saved_mouse_tracking = False
        self._saved_mouse_tracking_sgr = False
        self._bracketed_paste_mode = False
        self._current_directory = None

        self._current_size = self._calculate_size()

        # Connect style changed signal
        self._style_manager.style_changed.connect(self._handle_style_changed)

    def _update_default_format(self):
        """Update the default text format based on current style."""
        self._default_text_format = QTextCharFormat()
        self._default_text_format.setForeground(self._style_manager.get_color(ColorRole.TEXT_PRIMARY))
        self._default_text_format.setBackground(self._style_manager.get_color(ColorRole.TAB_BACKGROUND_ACTIVE))
        self._default_text_format.setFontWeight(QFont.Normal)
        self._default_text_format.setFontUnderline(False)
        self._default_text_format.setFontItalic(False)
        # Clear any custom property markers
        for prop in FormatProperty:
            self._default_text_format.setProperty(prop, False)

    def _handle_style_changed(self):
        """Handle style changes."""
        # Update default format
        self._update_default_format()

        # Update current format while preserving custom properties
        new_format = QTextCharFormat(self._default_text_format)

        # Check each custom property and preserve if set
        if self._current_text_format.property(FormatProperty.CUSTOM_FOREGROUND):
            new_format.setForeground(self._current_text_format.foreground())
            new_format.setProperty(FormatProperty.CUSTOM_FOREGROUND, True)

        if self._current_text_format.property(FormatProperty.CUSTOM_BACKGROUND):
            new_format.setBackground(self._current_text_format.background())
            new_format.setProperty(FormatProperty.CUSTOM_BACKGROUND, True)

        if self._current_text_format.property(FormatProperty.CUSTOM_WEIGHT):
            new_format.setFontWeight(self._current_text_format.fontWeight())
            new_format.setProperty(FormatProperty.CUSTOM_WEIGHT, True)

        if self._current_text_format.property(FormatProperty.CUSTOM_ITALIC):
            new_format.setFontItalic(self._current_text_format.fontItalic())
            new_format.setProperty(FormatProperty.CUSTOM_ITALIC, True)

        if self._current_text_format.property(FormatProperty.CUSTOM_UNDERLINE):
            new_format.setFontUnderline(self._current_text_format.fontUnderline())
            new_format.setProperty(FormatProperty.CUSTOM_UNDERLINE, True)

        self._current_text_format = new_format

        # Update appearance
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {self._style_manager.get_color_str(ColorRole.TAB_BACKGROUND_ACTIVE)};
                color: {self._style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
                border: none;
            }}
        """)

        # Update colors for all text blocks
        cursor = self.textCursor()
        saved_position = cursor.position()
        cursor.movePosition(QTextCursor.Start)

        while not cursor.atEnd():
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
            text_format = cursor.charFormat()
            new_format = QTextCharFormat(text_format)

            # Only update colors that aren't custom (i.e., are using defaults)
            if not text_format.property(FormatProperty.CUSTOM_FOREGROUND):
                new_format.setForeground(self._style_manager.get_color(ColorRole.TEXT_PRIMARY))

            if not text_format.property(FormatProperty.CUSTOM_BACKGROUND):
                new_format.setBackground(self._style_manager.get_color(ColorRole.TAB_BACKGROUND_ACTIVE))

            cursor.mergeCharFormat(new_format)
            cursor.clearSelection()

        # Restore cursor position
        cursor.setPosition(saved_position)
        self.setTextCursor(cursor)

    def _ensure_line_width(self, cursor: QTextCursor):
        """
        Ensure the line at cursor has the correct width by overwriting characters.

        Args:
            cursor: Cursor positioned on the line to adjust
        """
        if not self._current_size:
            return

        # Get start of line
        cursor.movePosition(QTextCursor.StartOfLine)

        # Overwrite each position with either existing character or space
        for i in range(self._current_size.cols):
            cursor.setPosition(cursor.block().position() + i)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)

            # If we're beyond the current line length, fill with spaces
            if i >= cursor.block().length() - 1:
                cursor.insertText(' ', self._current_text_format)
            else:
                # Keep existing character but ensure it has current format
                char = cursor.selectedText()
                cursor.insertText(char, self._current_text_format)

    def _update_cursor_position(self, row: int, col: int):
        """
        Update cursor position and ensure it's within bounds.

        Args:
            row: Target row (0-based)
            col: Target column (0-based)
        """
        if not self._current_size:
            return

        # If the cursor was visible before we need to erase it
        if self._cursor_visible:
            old_cursor_rect = self._get_cursor_rect()
            if old_cursor_rect:
                self.viewport().update(old_cursor_rect)

        # Update cursor position
        self._cursor_row = max(0, row)
        self._cursor_col = max(0, min(col, self._current_size.cols - 1))

        # Update new cursor position
        new_cursor_rect = self._get_cursor_rect()
        if new_cursor_rect:
            self.viewport().update(new_cursor_rect)

    def _write_char(self, text: str):
        """
        Write a character at the current cursor position.

        Args:
            text: Character to write at current cursor position
        """
        if not self._current_size:
            return

        cursor = self.textCursor()
        cursor.beginEditBlock()

        try:
            # Calculate target block position
            first_visible = self.firstVisibleBlock().blockNumber()
            target_block = first_visible + self._cursor_row

            if text == '\r':
                self._update_cursor_position(self._cursor_row, 0)

            elif text == '\n':
                # Handle newline with scroll region if set
                if self._scroll_region is not None:
                    top, bottom = self._scroll_region
                    if self._cursor_row == bottom:
                        self._scroll_region_up(top, bottom)
                    else:
                        self._update_cursor_position(self._cursor_row + 1, self._cursor_col)
                else:
                    self._update_cursor_position(self._cursor_row + 1, self._cursor_col)

            elif text == '\b':
                self._update_cursor_position(self._cursor_row, self._cursor_col - 1)

            elif text == '\t':
                spaces = 8 - (self._cursor_col % 8)
                new_col = self._cursor_col + spaces

                if new_col < self._current_size.cols:
                    self._update_cursor_position(self._cursor_row, new_col)

            else:
                print(f"tb {target_block} of {self.document().blockCount()}, col: {self._cursor_col}: '{text}'")

                # Move to position
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.NextBlock, n=target_block)
                cursor.movePosition(QTextCursor.StartOfLine)
                cursor.movePosition(QTextCursor.Right, n=self._cursor_col)

                # Select and replace the character at current position
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                cursor.insertText(text, self._current_text_format)

                # Update cursor position with wrapping
                if self._cursor_col >= self._current_size.cols - 1:
                    self._update_cursor_position(self._cursor_row + 1, 0)
                else:
                    self._update_cursor_position(self._cursor_row, self._cursor_col + 1)

        finally:
            cursor.endEditBlock()

        # Update cursor visual position
        cursor_rect = self._get_cursor_rect()
        if cursor_rect:
            self.viewport().update(cursor_rect)

    def _scroll_region_up(self, top: int, bottom: int):
        """
        Scroll the defined region up one line.

        Args:
            top: Top line of scroll region (0-based)
            bottom: Bottom line of scroll region (0-based)
        """
        # Update cursor position if in scroll region
        if top <= self._cursor_row <= bottom:
            if self._cursor_row == bottom:
                self._cursor_row = bottom
            else:
                self._cursor_row -= 1

        cursor = self.textCursor()
        cursor.beginEditBlock()

        try:
            # Calculate block numbers for the scroll region
            first_visible = self.firstVisibleBlock().blockNumber()
            top_block = first_visible + top
            bottom_block = first_visible + bottom

            # Store first line's position to return to
            first_line_pos = cursor.position()

            # Move lines up one by one
            for block_num in range(top_block, bottom_block):
                # Position at start of next line
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.NextBlock, n=block_num + 1)
                line_start = cursor.position()

                # Select each character of the line below and its format
                chars = []
                formats = []
                for i in range(self._current_size.cols):
                    cursor.setPosition(line_start + i)
                    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                    chars.append(cursor.selectedText())
                    formats.append(cursor.charFormat())
                    cursor.clearSelection()

                # Position at start of current line
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.NextBlock, n=block_num)

                # Overwrite each character with the ones from the line below
                for i in range(self._current_size.cols):
                    cursor.setPosition(cursor.block().position() + i)
                    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                    cursor.insertText(chars[i], formats[i])

            # Clear the last line with spaces
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.NextBlock, n=bottom_block)
            for i in range(self._current_size.cols):
                cursor.setPosition(cursor.block().position() + i)
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                cursor.insertText(' ', self._current_text_format)

            # Return to original position
            cursor.setPosition(first_line_pos)

        finally:
            cursor.endEditBlock()


    def _scroll_region_down(self, top: int, bottom: int):
        """
        Scroll the defined region down one line.

        Args:
            top: Top line of scroll region (0-based)
            bottom: Bottom line of scroll region (0-based)
        """
        # Update cursor position if in scroll region
        if top <= self._cursor_row <= bottom:
            if self._cursor_row == top:
                self._cursor_row = top
            else:
                self._cursor_row += 1

        cursor = self.textCursor()
        cursor.beginEditBlock()

        try:
            # Calculate block numbers for the scroll region
            first_visible = self.firstVisibleBlock().blockNumber()
            top_block = first_visible + top
            bottom_block = first_visible + bottom

            # Store first line's position to return to
            first_line_pos = cursor.position()

            # Move lines down one by one, starting from bottom
            for block_num in range(bottom_block, top_block, -1):
                # Position at start of current line
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.NextBlock, n=block_num - 1)
                line_start = cursor.position()

                # Select each character of the current line and its format
                chars = []
                formats = []
                for i in range(self._current_size.cols):
                    cursor.setPosition(line_start + i)
                    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                    chars.append(cursor.selectedText())
                    formats.append(cursor.charFormat())
                    cursor.clearSelection()

                # Position at start of next line
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.NextBlock, n=block_num)

                # Overwrite each character in the line below
                for i in range(self._current_size.cols):
                    cursor.setPosition(cursor.block().position() + i)
                    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                    cursor.insertText(chars[i], formats[i])

            # Clear the first line with spaces
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.NextBlock, n=top_block)
            for i in range(self._current_size.cols):
                cursor.setPosition(cursor.block().position() + i)
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                cursor.insertText(' ', self._current_text_format)

            # Return to original position
            cursor.setPosition(first_line_pos)

        finally:
            cursor.endEditBlock()

    def _move_cursor_relative(self, rows: int, cols: int):
        """
        Move cursor relative to current position.

        Args:
            rows: Number of rows to move (negative = up)
            cols: Number of columns to move (negative = left)
        """
        self._update_cursor_position(
            self._cursor_row + rows,
            self._cursor_col + cols
        )

    def _handle_cursor_sequence(self, sequence: str):
        """Handle cursor movement sequences (CUU, CUD, CUF, CUB)."""
        match = re.match(r'\x1b\[(\d*)([ABCD])', sequence)
        if not match:
            self._logger.warning(f"Invalid cursor movement sequence: {sequence}")
            return

        count = int(match.group(1)) if match.group(1) else 1
        direction = match.group(2)

        if direction == 'A':  # Up
            self._move_cursor_relative(-count, 0)
        elif direction == 'B':  # Down
            self._move_cursor_relative(count, 0)
        elif direction == 'C':  # Forward
            self._move_cursor_relative(0, count)
        elif direction == 'D':  # Back
            self._move_cursor_relative(0, -count)

    def _handle_cursor_position(self, params: str):
        """Handle cursor position (CUP/HVP) sequences."""
        try:
            if not params:
                self._update_cursor_position(0, 0)
                return

            parts = params.split(';')
            row = int(parts[0]) - 1  # Convert to 0-based
            col = int(parts[1]) - 1 if len(parts) > 1 else 0

            self._update_cursor_position(row, col)

        except (ValueError, IndexError) as e:
            self._logger.warning(f"Invalid cursor position parameters: {params}, error: {e}")

    def _handle_clear_screen(self, params: str):
        """Handle clear screen (ED) sequences."""
        param = params if params else '0'
        cursor = self.textCursor()
        saved_position = cursor.position()  # Save exact position
        cursor.beginEditBlock()

        try:
            if param == '0':  # Clear from cursor to end of screen
                # Clear to end of current line
                col = cursor.columnNumber()
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                cursor.insertText(' ' * (self._current_size.cols - col), self._current_text_format)

                # Clear all lines below
                current_pos = cursor.position()
                cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()

                # Fill cleared area with empty lines
                cursor.setPosition(current_pos)
                block_num = cursor.blockNumber()
                total_blocks = self._current_size.rows - (block_num - self.firstVisibleBlock().blockNumber())
                for _ in range(total_blocks - 1):
                    cursor.insertText('\n' + ' ' * self._current_size.cols, self._current_text_format)

            elif param == '1':  # Clear from cursor to beginning of screen
                # Clear to start of current line
                col = cursor.columnNumber()
                cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                cursor.insertText(' ' * col, self._current_text_format)

                # Clear all lines above
                pos = cursor.position()
                cursor.movePosition(QTextCursor.Start, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()

                # Fill cleared area with empty lines
                cursor.setPosition(cursor.position())
                block_num = cursor.blockNumber()
                for _ in range(block_num):
                    cursor.insertText(' ' * self._current_size.cols + '\n', self._current_text_format)

            elif param == '2':  # Clear entire screen
                cursor.select(QTextCursor.Document)
                cursor.removeSelectedText()

                # Fill with empty lines
                for i in range(self._current_size.rows):
                    if i > 0:
                        cursor.insertText('\n')
                    cursor.insertText(' ' * self._current_size.cols, self._current_text_format)

            elif param == '3':  # Clear scrollback buffer
                if not self._using_alternate_screen:
                    # Save visible content
                    visible_start = self.firstVisibleBlock().position()
                    visible_end = self.lastVisibleBlock().position() + self.lastVisibleBlock().length()
                    cursor.setPosition(visible_start)
                    cursor.setPosition(visible_end, QTextCursor.KeepAnchor)
                    visible_content = cursor.selectedText()

                    # Clear everything
                    cursor.select(QTextCursor.Document)
                    cursor.removeSelectedText()

                    # Restore visible content
                    cursor.insertText(visible_content)

        finally:
            cursor.endEditBlock()
            cursor.setPosition(saved_position)  # Restore exact position
            self.setTextCursor(cursor)

    def _handle_erase_in_line(self, params: str):
        """Handle erase in line (EL) sequences."""
        param = params if params else '0'
        cursor = self.textCursor()
        saved_position = cursor.position()  # Save exact position
        cursor.beginEditBlock()

        try:
            if param == '0':  # Clear from cursor to end of line
                col = cursor.columnNumber()
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                cursor.insertText(' ' * (self._current_size.cols - col), self._current_text_format)
            elif param == '1':  # Clear from cursor to start of line
                col = cursor.columnNumber()
                cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                cursor.insertText(' ' * col, self._current_text_format)
            elif param == '2':  # Clear entire line
                cursor.movePosition(QTextCursor.StartOfLine)
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                cursor.insertText(' ' * self._current_size.cols, self._current_text_format)

        finally:
            cursor.endEditBlock()
            cursor.setPosition(saved_position)  # Restore exact position
            self.setTextCursor(cursor)

    def _handle_insert_delete(self, command: str, params: str):
        """Handle insert and delete operations."""
        count = int(params) if params else 1
        cursor = self.textCursor()
        cursor.beginEditBlock()

        try:
            # Calculate target position from tracked cursor
            first_visible = self.firstVisibleBlock().blockNumber()
            target_block = first_visible + self._cursor_row

            # Move Qt cursor to current input position
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.NextBlock, n=target_block)
            cursor.movePosition(QTextCursor.StartOfLine)
            cursor.movePosition(QTextCursor.Right, n=self._cursor_col)

            if command == '@':  # Insert blank characters
                remaining_space = self._current_size.cols - self._cursor_col
                insert_count = min(count, remaining_space)

                # Select characters to shift
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor,
                                remaining_space)
                existing_text = cursor.selectedText()

                # Overwrite with spaces and shifted text
                cursor.setPosition(cursor.block().position() + self._cursor_col)
                for i in range(insert_count):
                    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                    cursor.insertText(' ', self._current_text_format)

                # Write shifted text
                shifted_text = existing_text[:remaining_space - insert_count]
                for char in shifted_text:
                    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                    cursor.insertText(char, self._current_text_format)

            elif command == 'P':  # Delete characters
                # Select and remove characters, filling with spaces
                for i in range(count):
                    if self._cursor_col + i < self._current_size.cols:
                        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                        cursor.insertText(' ', self._current_text_format)

            elif command == 'L':  # Insert lines
                current_row = self._cursor_row
                for _ in range(count):
                    # Move content down
                    self._scroll_region_down(current_row, self._current_size.rows - 1)
                # Cursor position stays the same

            elif command == 'M':  # Delete lines
                current_row = self._cursor_row
                for _ in range(count):
                    # Move content up
                    self._scroll_region_up(current_row, self._current_size.rows - 1)
                # Cursor position stays the same

        finally:
            cursor.endEditBlock()

    def _handle_sgr_sequence(self, params: str):
        """Handle Select Graphic Rendition (SGR) sequences."""
        if not params:
            params = '0'  # Reset to default

        # Start with a copy of the current format
        current_format = QTextCharFormat(self._current_text_format)

        for param in params.split(';'):
            try:
                code = int(param)
            except ValueError:
                continue

            if code == 0:  # Reset all attributes
                current_format = QTextCharFormat(self._default_text_format)
                # Clear all custom property markers
                for prop in FormatProperty:
                    current_format.setProperty(prop, False)

            elif code == 1:  # Bold
                current_format.setFontWeight(QFont.Bold)
                current_format.setProperty(FormatProperty.CUSTOM_WEIGHT, True)

            elif code == 2:  # Faint
                current_format.setFontWeight(QFont.Light)
                current_format.setProperty(FormatProperty.CUSTOM_WEIGHT, True)

            elif code == 3:  # Italic
                current_format.setFontItalic(True)
                current_format.setProperty(FormatProperty.CUSTOM_ITALIC, True)

            elif code == 4:  # Underline
                current_format.setFontUnderline(True)
                current_format.setProperty(FormatProperty.CUSTOM_UNDERLINE, True)

            elif code == 22:  # Normal intensity
                current_format.setFontWeight(QFont.Normal)
                current_format.setProperty(FormatProperty.CUSTOM_WEIGHT, False)

            elif code == 23:  # Not italic
                current_format.setFontItalic(False)
                current_format.setProperty(FormatProperty.CUSTOM_ITALIC, False)

            elif code == 24:  # Not underlined
                current_format.setFontUnderline(False)
                current_format.setProperty(FormatProperty.CUSTOM_UNDERLINE, False)

            elif code == 39:  # Default foreground color
                current_format.setForeground(self._style_manager.get_color(ColorRole.TEXT_PRIMARY))
                current_format.setProperty(FormatProperty.CUSTOM_FOREGROUND, False)

            elif code == 49:  # Default background color
                current_format.setBackground(self._style_manager.get_color(ColorRole.TAB_BACKGROUND_ACTIVE))
                current_format.setProperty(FormatProperty.CUSTOM_BACKGROUND, False)

            # Foreground colors
            elif 30 <= code <= 37:
                color_roles = [
                    ColorRole.TERM_BLACK,
                    ColorRole.TERM_RED,
                    ColorRole.TERM_GREEN,
                    ColorRole.TERM_YELLOW,
                    ColorRole.TERM_BLUE,
                    ColorRole.TERM_MAGENTA,
                    ColorRole.TERM_CYAN,
                    ColorRole.TERM_WHITE
                ]
                current_format.setForeground(self._style_manager.get_color(color_roles[code - 30]))
                current_format.setProperty(FormatProperty.CUSTOM_FOREGROUND, True)

            # Bright foreground colors
            elif 90 <= code <= 97:
                color_roles = [
                    ColorRole.TERM_BRIGHT_BLACK,
                    ColorRole.TERM_BRIGHT_RED,
                    ColorRole.TERM_BRIGHT_GREEN,
                    ColorRole.TERM_BRIGHT_YELLOW,
                    ColorRole.TERM_BRIGHT_BLUE,
                    ColorRole.TERM_BRIGHT_MAGENTA,
                    ColorRole.TERM_BRIGHT_CYAN,
                    ColorRole.TERM_BRIGHT_WHITE
                ]
                current_format.setForeground(self._style_manager.get_color(color_roles[code - 90]))
                current_format.setProperty(FormatProperty.CUSTOM_FOREGROUND, True)

            # Background colors
            elif 40 <= code <= 47:
                color_roles = [
                    ColorRole.TERM_BLACK,
                    ColorRole.TERM_RED,
                    ColorRole.TERM_GREEN,
                    ColorRole.TERM_YELLOW,
                    ColorRole.TERM_BLUE,
                    ColorRole.TERM_MAGENTA,
                    ColorRole.TERM_CYAN,
                    ColorRole.TERM_WHITE
                ]
                current_format.setBackground(self._style_manager.get_color(color_roles[code - 40]))
                current_format.setProperty(FormatProperty.CUSTOM_BACKGROUND, True)

            # Bright background colors
            elif 100 <= code <= 107:
                color_roles = [
                    ColorRole.TERM_BRIGHT_BLACK,
                    ColorRole.TERM_BRIGHT_RED,
                    ColorRole.TERM_BRIGHT_GREEN,
                    ColorRole.TERM_BRIGHT_YELLOW,
                    ColorRole.TERM_BRIGHT_BLUE,
                    ColorRole.TERM_BRIGHT_MAGENTA,
                    ColorRole.TERM_BRIGHT_CYAN,
                    ColorRole.TERM_BRIGHT_WHITE
                ]
                current_format.setBackground(self._style_manager.get_color(color_roles[code - 100]))
                current_format.setProperty(FormatProperty.CUSTOM_BACKGROUND, True)

        self._current_text_format = current_format

    def _handle_screen_buffer_switch(self, enable_alternate: bool):
        """Handle switching between main and alternate screen buffers."""
        if enable_alternate == self._using_alternate_screen:
            return

        cursor = self.textCursor()

        if enable_alternate:
            # Save main screen content and cursor position
            self._main_screen_buffer = self.toPlainText()
            self._saved_cursor_position = (
                cursor.blockNumber() - self.firstVisibleBlock().blockNumber(),
                cursor.columnNumber()
            )

            # Clear screen for alternate buffer
            self.clear()
            cursor = self.textCursor()

            # Initialize alternate screen with empty lines
            if self._current_size:
                empty_line = ' ' * self._current_size.cols
                for _ in range(self._current_size.rows):
                    cursor.insertText(empty_line + '\n', self._current_text_format)
                cursor.movePosition(QTextCursor.Start)
                self.setTextCursor(cursor)

            self._using_alternate_screen = True

        else:
            # Save alternate screen content
            self._alternate_screen_buffer = self.toPlainText()

            # Restore main screen
            self.clear()
            cursor = self.textCursor()
            cursor.insertText(self._main_screen_buffer)

            # Restore cursor position
            if self._saved_cursor_position:
                row, col = self._saved_cursor_position
                self._move_cursor_to(row, col)

            self._using_alternate_screen = False

    def _calculate_size(self) -> TerminalSize:
        """Calculate current terminal size in rows and columns."""
        fm = QFontMetricsF(self.font())
        char_width = int(fm.horizontalAdvance(' ') + 0.999)
        char_height = int(fm.height() + 0.999)

        if char_width == 0 or char_height == 0:
            self._logger.warning(f"Invalid character dimensions: width={char_width}, height={char_height}")
            return TerminalSize(24, 80)  # Default fallback size

        viewport = self.viewport()
        viewport_width = viewport.width()
        viewport_height = viewport.height()

        # Calculate rows and columns
        cols = max(viewport_width // char_width, 1)
        rows = max(viewport_height // char_height, 1)

        print(f"calculate size {rows}x{cols}")
        return TerminalSize(rows, cols)

    def _blink_cursor(self):
        """Toggle cursor visibility for blinking effect."""
        self._cursor_visible = not self._cursor_visible
        # Only force update of the cursor area
        cursor_rect = self._get_cursor_rect()
        if cursor_rect:
            self.viewport().update(cursor_rect)

    def _get_cursor_rect(self) -> Optional[QRect]:
        """
        Get the rectangle where the cursor should be drawn.

        Returns:
            QRect: Rectangle defining cursor position and size, or None if dimensions invalid

        Note:
            Coordinates are in viewport coordinates relative to the widget
        """
        if not self._current_size:
            return None

        # Calculate cursor rectangle based on character metrics
        fm = QFontMetricsF(self.font())
        char_width = int(fm.horizontalAdvance(' ') + 0.999)
        char_height = int(fm.height() + 0.999)

        # Calculate position based on cursor row/col
        content_offset = self.contentOffset()
        x = content_offset.x() + (self._cursor_col * char_width)
        cursor_row = self._cursor_row + self.firstVisibleBlock().blockNumber()
        y = content_offset.y() + (cursor_row * char_height)

        return QRect(
            round(x),
            round(y),
            char_width,
            char_height
        )

    def paintEvent(self, event: QPaintEvent):
        """
        Handle widget painting including cursor.

        Args:
            event: Paint event containing region to update
        """
        # Let Qt handle normal text rendering
        super().paintEvent(event)

        # Only draw cursor if visible
        if not self._cursor_visible:
            return

        cursor_rect = self._get_cursor_rect()
        if not cursor_rect or not cursor_rect.intersects(event.rect()):
            return

        # Get text cursor at input position to read character/format
        cursor = self.textCursor()
        first_visible = self.firstVisibleBlock().blockNumber()
        target_block = first_visible + self._cursor_row

        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.NextBlock, n=target_block)
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.movePosition(QTextCursor.Right, n=self._cursor_col)

        # Get character under cursor
        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        char = cursor.selectedText()
        char_format = cursor.charFormat()

        painter = QPainter(self.viewport())

        # Get colors for inversion
        fg_color = char_format.foreground().color()
        bg_color = char_format.background().color()

        if not char or char == ' ':
            # Just draw solid cursor for empty space
            painter.fillRect(cursor_rect, self.palette().text())
        else:
            # Draw inverted cursor with character
            painter.fillRect(cursor_rect, fg_color)  # Use text color as background
            painter.setPen(bg_color)  # Use background color as text color
            painter.setFont(self.font())
            painter.drawText(cursor_rect, 0, char)

    def _position_qt_cursor_at_input(self) -> QTextCursor:
        """Position Qt cursor at current input cursor position.

        Returns:
            QTextCursor: Cursor positioned at current input location
        """
        cursor = self.textCursor()
        first_visible = self.firstVisibleBlock().blockNumber()
        target_block = first_visible + self._cursor_row

        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.NextBlock, n=target_block)
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.movePosition(QTextCursor.Right, n=self._cursor_col)

        return cursor

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse events for selection."""
        if self._mouse_tracking:
            # Mouse tracking code remains the same
            pass
        else:
            # Let Qt handle text selection normally
            super().mousePressEvent(event)

        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse movement for selection."""
        if not self._mouse_tracking:
            super().mouseMoveEvent(event)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release for selection."""
        if self._mouse_tracking:
            # Mouse tracking code remains the same
            pass
        else:
            super().mouseReleaseEvent(event)
        event.accept()

    def resizeEvent(self, event: QResizeEvent):
        """Handle resize events."""
        super().resizeEvent(event)
        new_size = self._calculate_size()

        if self._current_size != new_size:
            old_size = self._current_size
            self._current_size = new_size
            self._logger.debug(
                f"Terminal size changed: {old_size} -> {new_size}"
            )
            self._reflow_content(old_size, new_size)
            self.size_changed.emit()

    def focusInEvent(self, event: QFocusEvent):
        """Handle focus in to start cursor blinking."""
        super().focusInEvent(event)
        self._cursor_blink_timer.start(500)
        self._cursor_visible = True
        self.setCursorWidth(0)  # Ensure Qt cursor stays hidden
        self.viewport().update()

    def focusOutEvent(self, event: QFocusEvent):
        """Handle focus out to stop cursor blinking."""
        super().focusOutEvent(event)
        self._cursor_blink_timer.stop()
        self._cursor_visible = False
        self.setCursorWidth(0)  # Ensure Qt cursor stays hidden
        self.viewport().update()

    def _reflow_content(self, old_size: Optional[TerminalSize], new_size: TerminalSize):
        """Reflow terminal content for new dimensions."""
        if not old_size:
            return

        cursor = self.textCursor()
        cursor.beginEditBlock()

        try:
            # Process each line
            cursor.movePosition(QTextCursor.Start)
            while not cursor.atEnd():
                # Get current line
                cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
                line = cursor.selectedText()

                # Pad or truncate line to new width
                if len(line) < new_size.cols:
                    cursor.insertText(line + ' ' * (new_size.cols - len(line)), self._current_text_format)
                else:
                    cursor.insertText(line[:new_size.cols], self._current_text_format)

                # Move to next line
                if not cursor.atEnd():
                    cursor.movePosition(QTextCursor.NextBlock)

            # Adjust cursor position if needed
            self._cursor_col = min(self._cursor_col, new_size.cols - 1)
            self._cursor_row = min(self._cursor_row, new_size.rows - 1)

        finally:
            cursor.endEditBlock()

        # Force cursor area update
        cursor_rect = self._get_cursor_rect()
        if cursor_rect:
            self.viewport().update(cursor_rect)

    def update_pty_size(self, fd: int) -> None:
        """Update PTY size using current terminal dimensions.

        Args:
            fd: File descriptor for PTY

        Raises:
            OSError: If ioctl call fails
        """
        try:
            size = self._calculate_size()
            fcntl.ioctl(fd, termios.TIOCSWINSZ, size.to_struct())
        except OSError as e:
            self._logger.error(f"Failed to update PTY size: {e}")
            raise

    def insertFromMimeData(self, source):
        """Handle paste events with support for bracketed paste mode."""
        if source.hasText():
            text = source.text()
            if self._bracketed_paste_mode:
                # Wrap the pasted text in bracketed paste sequences
                self.data_ready.emit(b'\x1b[200~')
                self.data_ready.emit(text.encode())
                self.data_ready.emit(b'\x1b[201~')
            else:
                self.data_ready.emit(text.encode())

    def clear(self):
        """Clear the terminal."""
        cursor = self.textCursor()
        cursor.select(QTextCursor.Document)
        cursor.removeSelectedText()

        # If we have a size, fill with empty lines
        if self._current_size:
            empty_line = ' ' * self._current_size.cols
            cursor.insertText(empty_line, self._current_text_format)
            for _ in range(self._current_size.rows - 1):
                cursor.insertText('\n' + empty_line, self._current_text_format)

            cursor.movePosition(QTextCursor.Start)
            self.setTextCursor(cursor)

    def put_data(self, data: bytes):
        """Display received data with ANSI sequence handling.

        Args:
            data: Raw bytes from terminal

        Raises:
            UnicodeDecodeError: If data cannot be decoded
        """
        text = data.decode(errors='replace')

        print(f"put data {repr(text)}")
        i = 0
        while i < len(text):
            char = text[i]

            if self._in_escape_seq:
                self._escape_seq_buffer += char

                # Process escape sequence when complete
                if self._is_escape_sequence_complete(self._escape_seq_buffer):
                    self._process_escape_sequence(self._escape_seq_buffer)
                    self._escape_seq_buffer = ""
                    self._in_escape_seq = False
                elif len(self._escape_seq_buffer) > 128:  # Safety limit
                    self._logger.warning(f"Escape sequence too long, discarding: {repr(self._escape_seq_buffer)}")
                    self._escape_seq_buffer = ""
                    self._in_escape_seq = False

            elif char == '\x1b':  # Start of new escape sequence
                self._in_escape_seq = True
                self._escape_seq_buffer = char

            else:
                self._write_char(char)

            i += 1

        self.ensureCursorVisible()

    def _is_escape_sequence_complete(self, sequence: str) -> bool:
        """Check if an escape sequence is complete.

        Args:
            sequence: The escape sequence to check

        Returns:
            bool: True if the sequence is complete
        """
        if len(sequence) < 2:
            return False

        if sequence.startswith('\x1b]'):  # OSC sequence
            return sequence.endswith('\x07') or sequence.endswith('\x1b\\')

        if sequence.startswith('\x1b['):  # CSI sequence
            return sequence[-1].isalpha() or sequence[-1] in '@`~'

        if sequence.startswith('\x1bP'):  # DCS sequence
            return sequence.endswith('\x1b\\')

        # Simple ESC sequences
        return len(sequence) == 2 and sequence[1] in '=>\7\\8cDEHM'

    def keyPressEvent(self, event: QKeyEvent):
        """Handle key press events including control sequences."""
        text = event.text()
        key = event.key()
        modifiers = event.modifiers()

        # Handle keypad in application mode
        if self._application_keypad_mode and not modifiers:
            # Map keypad keys to application mode sequences
            keypad_map = {
                Qt.Key_0: b'\x1bOp',
                Qt.Key_1: b'\x1bOq',
                Qt.Key_2: b'\x1bOr',
                Qt.Key_3: b'\x1bOs',
                Qt.Key_4: b'\x1bOt',
                Qt.Key_5: b'\x1bOu',
                Qt.Key_6: b'\x1bOv',
                Qt.Key_7: b'\x1bOw',
                Qt.Key_8: b'\x1bOx',
                Qt.Key_9: b'\x1bOy',
                Qt.Key_Minus: b'\x1bOm',
                Qt.Key_Plus: b'\x1bOl',
                Qt.Key_Period: b'\x1bOn',
                Qt.Key_Enter: b'\x1bOM',
            }

            if key in keypad_map:
                self.data_ready.emit(keypad_map[key])
                event.accept()
                return

        # Handle control key combinations
        if modifiers & Qt.ControlModifier:
            if key >= Qt.Key_A and key <= Qt.Key_Z:
                # Calculate control character (1-26)
                ctrl_char = bytes([key - Qt.Key_A + 1])
                self.data_ready.emit(ctrl_char)
                event.accept()
                return

            # Handle special control sequences
            ctrl_map = {
                Qt.Key_2: b'\x00',  # Ctrl+@, Ctrl+2
                Qt.Key_3: b'\x1b',  # Ctrl+[, Ctrl+3
                Qt.Key_4: b'\x1c',  # Ctrl+\, Ctrl+4
                Qt.Key_5: b'\x1d',  # Ctrl+], Ctrl+5
                Qt.Key_6: b'\x1e',  # Ctrl+^, Ctrl+6
                Qt.Key_7: b'\x1f',  # Ctrl+_, Ctrl+7
                Qt.Key_8: b'\x7f',  # Ctrl+8 (delete)
            }
            if key in ctrl_map:
                self.data_ready.emit(ctrl_map[key])
                event.accept()
                return

        # Handle application cursor key mode and normal mode
        if self._application_cursor_keys:
            # Handle cursor keys in application mode
            if key == Qt.Key_Up:
                self.data_ready.emit(b'\x1bOA')
            elif key == Qt.Key_Down:
                self.data_ready.emit(b'\x1bOB')
            elif key == Qt.Key_Right:
                self.data_ready.emit(b'\x1bOC')
            elif key == Qt.Key_Left:
                self.data_ready.emit(b'\x1bOD')
            elif key == Qt.Key_Return or key == Qt.Key_Enter:
                self.data_ready.emit(b'\r')
            elif key == Qt.Key_Backspace:
                self.data_ready.emit(b'\x7f' if modifiers & Qt.ControlModifier else b'\b')
            elif key == Qt.Key_Delete:
                self.data_ready.emit(b'\x1b[3~')
            elif text:
                self.data_ready.emit(text.encode())
        else:
            # Normal mode key handling
            if key == Qt.Key_Up:
                self.data_ready.emit(b'\x1b[A')
            elif key == Qt.Key_Down:
                self.data_ready.emit(b'\x1b[B')
            elif key == Qt.Key_Right:
                self.data_ready.emit(b'\x1b[C')
            elif key == Qt.Key_Left:
                self.data_ready.emit(b'\x1b[D')
            elif key == Qt.Key_Return or key == Qt.Key_Enter:
                self.data_ready.emit(b'\r')
            elif key == Qt.Key_Backspace:
                self.data_ready.emit(b'\x7f' if modifiers & Qt.ControlModifier else b'\b')
            elif key == Qt.Key_Delete:
                self.data_ready.emit(b'\x1b[3~')
            elif key == Qt.Key_Tab:
                self.data_ready.emit(b'\t')
            elif text:
                self.data_ready.emit(text.encode())

        event.accept()

    def _process_escape_sequence(self, sequence: str):
        """Handle ANSI escape sequences.

        Args:
            sequence: The complete escape sequence starting with ESC
        """
        # Handle OSC sequences first
        if sequence.startswith('\x1b]'):
            if self._handle_osc_sequence(sequence):
                return

        # Handle Control Sequence Introducer (CSI) sequences
        if sequence.startswith('\x1b['):
            # Extract the command and parameters
            command = sequence[-1]
            params = sequence[2:-1]  # Remove ESC[ and command char

            # Handle based on command type
            if command in 'ABCD':  # Cursor movement
                self._handle_cursor_sequence(sequence)
                return

            if command in 'Hf':  # Cursor position
                self._handle_cursor_position(params)
                return

            if command == 'J':  # Clear screen
                self._handle_clear_screen(params)
                return

            if command == 'K':  # Erase in line
                self._handle_erase_in_line(params)
                return

            if command in '@PML':  # Insert/Delete operations
                self._handle_insert_delete(command, params)
                return

            if command == 'm':  # SGR - Select Graphic Rendition
                self._handle_sgr_sequence(params)
                return

            if command == 'n':  # Device Status Reports
                self._handle_device_status(params)
                return

            if command == 'c':  # Device Attributes
                self._handle_device_attributes(params)
                return

            if command == 'g':  # Tab Controls
                self._handle_tab_control(params)
                return

            if command == 'r':  # Scrolling Region
                self._handle_scroll_region(params)
                return

            if command in 'hl':  # Mode Settings
                self._handle_mode_setting(command, params)
                return

            # Handle window operations
            if command == 't':
                self._handle_window_operation(params)
                return

        # Handle keypad mode sequences
        if sequence == '\x1b=':  # Enable application keypad mode
            self._application_keypad_mode = True
            return

        if sequence == '\x1b>':  # Disable application keypad mode
            self._application_keypad_mode = False
            return

        # Handle single-character sequences
        if len(sequence) == 2:  # ESC + one character
            if self._handle_simple_sequence(sequence[1]):
                return

        self._logger.warning(f"Unhandled escape sequence: {sequence}")

    def _handle_device_status(self, params: str):
        """Handle Device Status Report (DSR) sequences."""
        if params == '5':  # Device status report
            self.data_ready.emit(b'\x1b[0n')  # Device OK
        elif params == '6':  # Cursor position report
            cursor = self.textCursor()
            row = cursor.blockNumber() - self.firstVisibleBlock().blockNumber() + 1
            col = cursor.columnNumber() + 1
            self.data_ready.emit(f'\x1b[{row};{col}R'.encode())

    def _handle_device_attributes(self, params: str):
        """Handle Device Attributes (DA) sequences."""
        if not params or params == '0':
            # Report as VT100 with Advanced Video Option
            self.data_ready.emit(b'\x1b[?1;2c')
        elif params == '>':  # Secondary Device Attributes
            # Report as VT220
            self.data_ready.emit(b'\x1b[>1;10;0c')

    def _handle_tab_control(self, params: str):
        """Handle tab control sequences."""
        if not params or params == '0':  # Clear tab at cursor
            pass  # Implement tab clear
        elif params == '3':  # Clear all tabs
            pass  # Implement clear all tabs

    def _handle_scroll_region(self, params: str):
        """Handle scrolling region (DECSTBM) sequences."""
        try:
            if params:
                top, bottom = map(lambda x: int(x) - 1, params.split(';'))
                self._scroll_region = (top, bottom)
            else:
                self._scroll_region = None
        except (ValueError, IndexError):
            self._scroll_region = None

    def _handle_mode_setting(self, command: str, params: str):
        """Handle mode setting sequences."""
        set_mode = (command == 'h')
        if params.startswith('?'):  # Private modes
            self._handle_private_mode(params[1:], set_mode)
        else:  # ANSI modes
            self._handle_ansi_mode(params, set_mode)

    def _handle_private_mode(self, mode: str, set_mode: bool):
        """Handle private mode settings."""
        if mode == '1':  # Application Cursor Keys
            self._application_cursor_keys = set_mode
        elif mode == '7':  # Auto-wrap Mode
            self.setLineWrapMode(
                QPlainTextEdit.WidgetWidth if set_mode
                else QPlainTextEdit.NoWrap
            )
        elif mode == '12':  # Send/receive (SRM)
            pass  # Not implemented
        elif mode == '25':  # Show/Hide Cursor
            self.setCursorWidth(8 if set_mode else 0)
        elif mode == '1049':  # Alternate Screen Buffer
            self._handle_screen_buffer_switch(set_mode)
        elif mode == '2004':  # Bracketed Paste Mode
            self._bracketed_paste_mode = set_mode
        elif mode in ('1001s', '1001r', '1001', '1002', '1006'):  # Mouse tracking modes
            self._handle_mouse_tracking_mode(mode, set_mode)

    def _handle_mouse_tracking_mode(self, mode: str, set_mode: bool):
        """Handle mouse tracking mode settings."""
        if mode == '1001s':  # Save mouse tracking state
            self._saved_mouse_tracking = self._mouse_tracking
            self._saved_mouse_tracking_sgr = self._mouse_tracking_sgr
        elif mode == '1001r':  # Restore mouse tracking state
            self._mouse_tracking = self._saved_mouse_tracking
            self._mouse_tracking_sgr = self._saved_mouse_tracking_sgr
        elif mode == '1001':  # Toggle mouse tracking
            self._mouse_tracking = set_mode
        elif mode == '1002':  # Enable mouse button tracking
            self._mouse_tracking = set_mode
        elif mode == '1006':  # Enable SGR mouse mode
            self._mouse_tracking_sgr = set_mode

    def _handle_ansi_mode(self, mode: str, set_mode: bool):
        """Handle ANSI mode settings."""
        if mode == '4':  # Insert Mode
            self.setOverwriteMode(not set_mode)
        elif mode == '20':  # Automatic Newline
            pass  # Not implemented

    def _handle_simple_sequence(self, char: str) -> bool:
        """Handle simple ESC + char sequences."""
        if char == '7':  # Save Cursor
            self._saved_cursor_position = (self._cursor_row, self._cursor_col)
            return True

        if char == '8':  # Restore Cursor
            if self._saved_cursor_position:
                row, col = self._saved_cursor_position
                self._update_cursor_position(row, col)
            return True

        if char == 'D':  # Index - Move cursor down one line
            cursor = self.textCursor()
            if cursor.blockNumber() == self.document().blockCount() - 1:
                cursor.insertText('\n' + ' ' * self._current_size.cols)

            cursor.movePosition(QTextCursor.Down)
            self.setTextCursor(cursor)
            return True

        if char == 'M':  # Reverse Index
            cursor = self.textCursor()
            if cursor.blockNumber() == 0:
                cursor.movePosition(QTextCursor.Start)
                cursor.insertText('\n' + ' ' * self._current_size.cols)
                cursor.movePosition(QTextCursor.Up)
            else:
                cursor.movePosition(QTextCursor.Up)
            self.setTextCursor(cursor)
            return True

        if char == 'E':  # Next Line
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.NextBlock)
            cursor.movePosition(QTextCursor.StartOfLine)
            self.setTextCursor(cursor)
            return True

        if char == 'c':  # Reset to Initial State
            self.clear()
            self._current_text_format = QTextCharFormat(self._default_text_format)
            self._scroll_region = None
            self._saved_cursor_position = None
            self._application_cursor_keys = False
            self._application_keypad_mode = False
            self._bracketed_paste_mode = False
            self.setOverwriteMode(True)
            return True

        return False

    def _handle_osc_sequence(self, sequence: str) -> bool:
        """Handle Operating System Command (OSC) sequences."""
        # Extract the OSC command number and parameter
        parts = sequence[2:].split(';', 1)
        if not parts:
            return False

        try:
            command = int(parts[0])
            param = parts[1][:-1] if len(parts) > 1 else ''  # Remove terminator

            if command == 0:  # Window title
                self._logger.debug(f"Window title set to: {param}")
                return True

            if command == 7:  # Current directory notification
                if param == 'f':  # Query current directory
                    if self._current_directory:
                        response = f"\x1b]7;{self._current_directory}\x1b\\"
                        self.data_ready.emit(response.encode())
                else:
                    self._current_directory = param
                    self._logger.debug(f"Current directory set to: {param}")
                return True

            if command in (10, 11):  # Set foreground/background color
                self._logger.debug(f"Set {'foreground' if command == 10 else 'background'} color: {param}")
                return True

        except (ValueError, IndexError) as e:
            self._logger.warning(f"Failed to parse OSC sequence: {sequence}, error: {e}")

        return False
