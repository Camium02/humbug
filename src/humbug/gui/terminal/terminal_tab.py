"""Terminal tab implementation."""

import asyncio
from asyncio.subprocess import Process
import logging
import os
import select
import signal
from typing import Dict, Optional, Set

from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtCore import Slot
from PySide6.QtGui import QFont

from humbug.gui.tab_base import TabBase
from humbug.gui.tab_state import TabState
from humbug.gui.tab_type import TabType
from humbug.gui.color_role import ColorRole
from humbug.gui.style_manager import StyleManager
from humbug.gui.terminal.terminal_widget import TerminalWidget
from humbug.gui.status_message import StatusMessage


class TerminalTab(TabBase):
    """Tab containing a terminal emulator."""

    def __init__(self, tab_id: str, command: Optional[str] = None, parent=None):
        """
        Initialize terminal tab.

        Args:
            tab_id: Unique identifier for this tab
            command: Optional command to run in terminal
            parent: Optional parent widget
        """
        super().__init__(tab_id, parent)
        self._logger = logging.getLogger("TerminalTab")
        self._command = command
        self._style_manager = StyleManager()

        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create terminal widget
        self._terminal = TerminalWidget(self)
        layout.addWidget(self._terminal)

        # Connect signals
        self._terminal.data_ready.connect(self._handle_data_ready)

        # Handle style changes
        self._style_manager.style_changed.connect(self._handle_style_changed)
        self._handle_style_changed()

        # Install activation tracking for the terminal
        self._install_activation_tracking(self._terminal)

        # Initialize process and task tracking
        self._process: Optional[Process] = None
        self._tasks: Set[asyncio.Task] = set()
        self._running = True
        self._master_fd = None

        # Initialize window size handling
        self._install_sigwinch_handler()
        self._terminal.size_changed.connect(self._handle_terminal_resize)

        # Start local shell process
        self._create_tracked_task(self._start_process())

    def _install_sigwinch_handler(self):
        """Install SIGWINCH handler for terminal size changes."""
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGWINCH, self._handle_terminal_resize)

    def _handle_terminal_resize(self):
        """Handle terminal window resize events."""
        if self._master_fd is not None:
            try:
                print("terminal resize")
                self._terminal.update_pty_size(self._master_fd)
            except OSError as e:
                self._logger.error(f"Failed to handle window resize: {e}")

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """
        Create a tracked asyncio task.

        Args:
            coro: Coroutine to create task from

        Returns:
            Created task
        """
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _start_process(self):
        """Start the terminal process."""
        try:
            self._logger.debug("Starting terminal process...")

            shell = os.environ.get('SHELL', '/bin/sh')

            # Create pseudo-terminal
            master_fd, slave_fd = pty.openpty()

            # Set raw mode
            mode = termios.tcgetattr(slave_fd)
            mode[3] &= ~(termios.ECHO | termios.ICANON)  # Turn off echo and canonical mode
            termios.tcsetattr(slave_fd, termios.TCSAFLUSH, mode)

            try:
                print("start process")
                self._terminal.update_pty_size(master_fd)
            except OSError as e:
                self._logger.warning(f"Failed to set initial terminal size: {e}")

            # Start process with the slave end of the pty
            self._process = await asyncio.create_subprocess_exec(
                shell,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True
            )

            # Close slave fd as the subprocess has it
            os.close(slave_fd)

            self._logger.debug(f"Process started with pid {self._process.pid}")

            # Create task for reading from master_fd
            self._create_tracked_task(self._read_loop(master_fd))

            # Store master fd for writing
            self._master_fd = master_fd

        except Exception as e:
            self._logger.error("Failed to start terminal process: %s", str(e))
            self._terminal.put_data(f"Failed to start terminal: {str(e)}\r\n".encode())

    async def _read_loop(self, master_fd):
        """Read data from the master end of the pty."""
        try:
            while self._running:
                try:
                    r, w, e = await asyncio.get_event_loop().run_in_executor(
                        None,
                        select.select,
                        [master_fd],
                        [],
                        [],
                        0.1  # Add timeout to allow checking _running
                    )

                    if not r:
                        continue

                    if master_fd in r:
                        try:
                            data = os.read(master_fd, 1024)
                            if not data:
                                break

                            self._terminal.put_data(data)
                        except OSError:
                            break
                except (OSError, select.error) as e:
                    if not self._running:
                        break
                    self._logger.error("Error reading from terminal: %s", str(e))
                    break
        except Exception as e:
            self._logger.error("Error in read loop: %s", str(e))
        finally:
            if self._master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    @Slot(bytes)
    def _handle_data_ready(self, data: bytes):
        """Handle data from terminal."""
        try:
            if self._master_fd is not None and self._running:
                os.write(self._master_fd, data)
        except Exception as e:
            self._logger.error("Failed to write to process: %s", str(e))

    def _handle_style_changed(self):
        """Handle style changes."""
        # Update terminal font
        font = QFont(self._style_manager.monospace_font_families)
        base_size = self._style_manager.base_font_size
        font.setPointSizeF(base_size * self._style_manager.zoom_factor)
        self._terminal.setFont(font)
        print(f"point size {base_size * self._style_manager.zoom_factor}")

        # Update terminal colors
        self._terminal.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {self._style_manager.get_color_str(ColorRole.TAB_BACKGROUND_ACTIVE)};
                color: {self._style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
                border: none;
                selection-background-color: {self._style_manager.get_color_str(ColorRole.TEXT_SELECTED)};
                selection-color: {self._style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
            }}
        """)

    async def _cleanup(self):
        """Clean up resources."""
        self._running = False

        # Cancel all pending tasks
        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Terminate process if it exists
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                self._process.kill()  # Force kill if terminate doesn't work
            except Exception as e:
                self._logger.error("Error terminating process: %s", str(e))
            self._process = None

        # Close master fd if it exists
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def get_state(self, temp_state: bool = False) -> TabState:
        """Get serializable state."""
        return TabState(
            type=TabType.TERMINAL,
            tab_id=self._tab_id,
            path="terminal://local",
            metadata={
                "command": self._command
            } if temp_state else None
        )

    @classmethod
    def restore_from_state(cls, state: TabState, parent=None) -> 'TerminalTab':
        """Restore terminal from saved state."""
        if state.type != TabType.TERMINAL:
            raise ValueError(f"Invalid tab type for TerminalTab: {state.type}")

        command = None
        if state.metadata:
            command = state.metadata.get("command")

        return cls(state.tab_id, command, parent)

    def set_cursor_position(self, position: Dict[str, int]) -> None:
        """Set cursor position."""
        self._terminal._update_cursor_position(
            position.get("line", 0),
            position.get("column", 0)
        )

    def get_cursor_position(self) -> Dict[str, int]:
        """Get current cursor position."""
        cursor = self._terminal.textCursor()
        return {
            "line": cursor.blockNumber(),
            "column": cursor.columnNumber()
        }

    def can_close(self) -> bool:
        """Check if terminal can be closed."""
        return True

    def close(self) -> None:
        """Close the terminal."""
        asyncio.create_task(self._cleanup())

    def can_save(self) -> bool:
        return False

    def save(self) -> bool:
        return True

    def can_save_as(self) -> bool:
        return False

    def save_as(self) -> bool:
        return True

    def can_undo(self) -> bool:
        return False

    def undo(self) -> None:
        pass

    def can_redo(self) -> bool:
        return False

    def redo(self) -> None:
        pass

    def can_cut(self) -> bool:
        return self._terminal.textCursor().hasSelection()

    def cut(self) -> None:
        self._terminal.cut()

    def can_copy(self) -> bool:
        return self._terminal.textCursor().hasSelection()

    def copy(self) -> None:
        self._terminal.copy()

    def can_paste(self) -> bool:
        return True

    def paste(self) -> None:
        self._terminal.paste()

    def show_find(self):
        """Show the find widget."""
        pass  # Terminal doesn't support find yet

    def can_submit(self) -> bool:
        return False

    def update_status(self) -> None:
        """Update status bar."""
        message = StatusMessage("Terminal: local")
        self.status_message.emit(message)
