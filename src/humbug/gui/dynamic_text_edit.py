"""Widget for displaying parts of individual chat messages."""

import logging

from PySide6.QtWidgets import (
    QFrame, QTextEdit, QSizePolicy
)
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QTextOption, QTextCursor, QTextCharFormat, QSyntaxHighlighter


class DynamicTextEdit(QTextEdit):
    """QTextEdit that automatically adjusts its height to content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.document().documentLayout().documentSizeChanged.connect(self._on_content_changed)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameStyle(QFrame.NoFrame)

        # Force the widget to always use the width of its container
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Set word wrap mode to adjust to widget width
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)

        # Batch update handling
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(50)
        self._update_timer.timeout.connect(self._process_delayed_update)
        self._pending_update = False

        # Track current content length for incremental updates
        self._current_length = 0

        self._logger = logging.getLogger("DynamicTextEdit")

    def wheelEvent(self, event):
        """Explicitly ignore wheel events to let them propagate up."""
        event.ignore()

    def _on_content_changed(self):
        """Queue a size update instead of processing immediately."""
        if not self._pending_update:
            self._pending_update = True
            self._update_timer.start()

    def _process_delayed_update(self):
        """Process the queued size update."""
        self._pending_update = False
        self.updateGeometry()

        # Ensure parent MessageWidget updates as well
        if self.parent():
            self.parent().updateGeometry()

    def resizeEvent(self, event):
        """Handle resize events."""
        super().resizeEvent(event)

        # Only update document width - let delayed update handle the rest
        self.document().setTextWidth(self.viewport().width())

    def set_incremental_text(self, text: str, text_format: QTextCharFormat = None):
        """Update text content incrementally by only adding new content."""
        self._logger.debug(f"inc text: '{text}'")
        if len(text) == self._current_length:
            # No new content
            return

        if len(text) < self._current_length:
            # Content is shorter than what we have - do a full reset
            self._logger.warning(f"text is shorter than before!: '{text}'")
            self.clear()
            self._current_length = 0
            return

        # Only insert the new content
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        if text_format:
            cursor.setCharFormat(text_format)
        new_text = text[self._current_length:]
        cursor.insertText(new_text)
        self._current_length = len(text)

    def clear(self):
        """Override clear to reset current length."""
        self.document().blockSignals(True)
        super().clear()
        self.document().blockSignals(False)
        self._current_length = 0
        self._on_content_changed()

    def minimumSizeHint(self) -> QSize:
        """Calculate minimum size based on content."""
        # Get the document height when wrapped to current width
        self.document().setTextWidth(self.viewport().width())
        height = int(self.document().size().height()) + 16

        # Use parent width for width calculation
        width = self.viewport().width()
        return QSize(width, height)

    def sizeHint(self) -> QSize:
        """Size hint is same as minimum size hint."""
        return self.minimumSizeHint()
