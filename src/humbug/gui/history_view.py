"""Chat history view widget."""

from typing import Optional, List

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget, QScrollArea, QSizePolicy
from PySide6.QtCore import Qt, QSize

from humbug.gui.message_widget import MessageWidget


class HistoryView(QScrollArea):
    """Read-only view for chat history."""

    def __init__(self, parent=None):
        """Initialize the history view."""
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        self.setWidgetResizable(True)

        # Disable scroll bars - parent handles scrolling
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Create container widget for messages
        self.container = QWidget(self)

        # Set size policies to ensure proper sizing
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)

        self.layout = QVBoxLayout(self.container)
        self.layout.setSpacing(4)  # Small gap between messages
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addStretch()  # Push messages to the top

        self.setWidget(self.container)

        # Track messages and current AI response
        self.messages: List[MessageWidget] = []
        self._ai_response_widget: Optional[MessageWidget] = None
        self._message_with_selection: Optional[MessageWidget] = None

        # Style the widgets
        self.setStyleSheet("""
            QScrollArea {
                background-color: black;
                border: none;
            }
            QWidget#container {
                background-color: black;
            }
        """)
        self.container.setObjectName("container")

    def append_message(self, message: str, style: str):
        """Append a message with the specified style."""
        msg_widget = MessageWidget(self)
        msg_widget.selectionChanged.connect(
            lambda has_selection: self._handle_selection_changed(msg_widget, has_selection)
        )
        msg_widget.set_content(message, style)

        # Add widget before the stretch spacer
        self.layout.insertWidget(self.layout.count() - 1, msg_widget)
        self.messages.append(msg_widget)

        if style == 'ai':
            self._ai_response_widget = msg_widget
        else:
            self._ai_response_widget = None

        # Update size after adding message
        self.updateGeometry()

    def update_last_ai_response(self, content: str):
        """Update the last AI response in the history."""
        if self._ai_response_widget:
            self._ai_response_widget.set_content(f"AI: {content}", 'ai')
        else:
            self.append_message(f"AI: {content}", 'ai')
        self.updateGeometry()

    def finish_ai_response(self):
        """Mark the current AI response as complete."""
        self._ai_response_widget = None

    def _handle_selection_changed(self, message_widget: MessageWidget, has_selection: bool):
        """Handle selection changes in message widgets."""
        if has_selection:
            if self._message_with_selection and self._message_with_selection != message_widget:
                old_cursor = self._message_with_selection.text_area.textCursor()
                old_cursor.clearSelection()
                self._message_with_selection.text_area.setTextCursor(old_cursor)
            self._message_with_selection = message_widget
        elif message_widget == self._message_with_selection:
            self._message_with_selection = None

    def has_selection(self) -> bool:
        """Check if any message has selected text."""
        return self._message_with_selection is not None and self._message_with_selection.has_selection()

    def copy_selection(self):
        """Copy selected text to clipboard."""
        if self._message_with_selection:
            self._message_with_selection.copy_selection()

    def sizeHint(self) -> QSize:
        """Calculate size based on content."""
        # Get the container's size hint
        size = self.container.sizeHint()
        # Use full width but calculated height
        return QSize(self.width(), size.height())

    def minimumSizeHint(self) -> QSize:
        """Minimum size is the same as size hint."""
        return self.sizeHint()
