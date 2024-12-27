"""Dialog for configuring conversation-specific settings."""

from typing import List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDoubleSpinBox
)
from PySide6.QtCore import Signal

from humbug.ai.conversation_settings import ConversationSettings
from humbug.gui.color_role import ColorRole
from humbug.gui.style_manager import StyleManager


class SettingsDialog(QDialog):
    """Dialog for editing conversation settings."""

    settings_changed = Signal(ConversationSettings)

    def __init__(self, parent=None):
        """Initialize the settings dialog."""
        super().__init__(parent)
        self.setWindowTitle("Conversation Settings")
        self.setMinimumWidth(400)
        self.setModal(True)

        self._available_models: List[str] = []
        self._initial_settings = None
        self._current_settings = None
        self._model_temperatures = {}

        style_manager = StyleManager()

        # Main layout with proper spacing
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        # Model selection
        model_layout = QHBoxLayout()
        model_label = QLabel("AI Model:")
        model_label.setMinimumHeight(40)
        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(200)
        self._model_combo.currentTextChanged.connect(self._handle_model_change)
        model_layout.addWidget(model_label)
        model_layout.addStretch()
        model_layout.addWidget(self._model_combo)
        layout.addLayout(model_layout)

        # Temperature setting
        temp_layout = QHBoxLayout()
        temp_label = QLabel("Temperature:")
        temp_label.setMinimumHeight(40)
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setDecimals(1)
        self.temp_spin.setMinimumWidth(200)
        temp_layout.addWidget(temp_label)
        temp_layout.addStretch()
        temp_layout.addWidget(self.temp_spin)
        layout.addLayout(temp_layout)

        # Add limits display
        self._limits_label = QLabel()
        self._limits_label.setMinimumHeight(40)
        layout.addWidget(self._limits_label)

        layout.addStretch()

        # Button row with proper spacing and alignment
        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)

        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")
        self.apply_button = QPushButton("Apply")

        self.ok_button.clicked.connect(self._handle_ok)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self._handle_apply)

        self._model_combo.currentTextChanged.connect(self._handle_value_change)
        self.temp_spin.valueChanged.connect(self._handle_value_change)

        # Set minimum button widths
        min_button_width = 80
        for button in [self.ok_button, self.cancel_button, self.apply_button]:
            button.setMinimumWidth(min_button_width)
            button.setContentsMargins(6, 6, 6, 6)
            button_layout.addWidget(button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

        # Apply consistent dialog styling
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {style_manager.get_color_str(ColorRole.BACKGROUND_DIALOG)};
            }}
            QLabel {{
                color: {style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
                background-color: {style_manager.get_color_str(ColorRole.BACKGROUND_DIALOG)};
            }}
            QComboBox {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND)};
                color: {style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
                border: none;
                border-radius: 4px;
                padding: 6px;
            }}
            QComboBox:disabled {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND_DISABLED)};
                color: {style_manager.get_color_str(ColorRole.TEXT_DISABLED)};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QDoubleSpinBox {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND)};
                color: {style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
                border: none;
                border-radius: 4px;
                padding: 6px;
            }}
            QDoubleSpinBox:disabled {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND_DISABLED)};
                color: {style_manager.get_color_str(ColorRole.TEXT_DISABLED)};
            }}
            QPushButton {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND)};
                color: {style_manager.get_color_str(ColorRole.TEXT_PRIMARY)};
                border: none;
                border-radius: 4px;
                padding: 6px;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND_HOVER)};
            }}
            QPushButton:pressed {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND_PRESSED)};
            }}
            QPushButton:disabled {{
                background-color: {style_manager.get_color_str(ColorRole.BUTTON_BACKGROUND_DISABLED)};
                color: {style_manager.get_color_str(ColorRole.TEXT_DISABLED)};
            }}
        """)

    def _handle_model_change(self, model: str):
        """Handle model selection changes."""
        supports_temp = ConversationSettings.supports_temperature(model)
        self.temp_spin.setEnabled(supports_temp)

        # Get and display model limits
        limits = ConversationSettings.get_model_limits(model)
        context_window = limits["context_window"]
        max_output = limits["max_output_tokens"]
        self._limits_label.setText(
            f"Context window: {context_window:,} tokens\n"
            f"Max output: {max_output:,} tokens"
        )

        if supports_temp:
            if model in self._model_temperatures:
                self.temp_spin.setValue(self._model_temperatures[model])
            else:
                # If we haven't stored a temperature for this model yet,
                # use the initial setting if it exists, otherwise default to 0.7
                if (self._initial_settings and
                    self._initial_settings.model == model and
                    self._initial_settings.temperature is not None):
                    self.temp_spin.setValue(self._initial_settings.temperature)
                else:
                    self.temp_spin.setValue(0.7)
        else:
            self.temp_spin.setValue(0.0)

        # Store the temperature for the current model if it supports it
        if supports_temp:
            self._model_temperatures[model] = self.temp_spin.value()

        self._handle_value_change()

    def _handle_value_change(self):
        """Handle changes to any setting value."""
        if not self._current_settings:
            return

        current_model = self._model_combo.currentText()
        current_temp = self.temp_spin.value() if ConversationSettings.supports_temperature(current_model) else None

        self.apply_button.setEnabled(
            current_model != self._current_settings.model or
            current_temp != self._current_settings.temperature
        )

    def set_available_models(self, models: List[str]):
        """
        Set the list of available models.

        Args:
            models: List of model names that are available for use
        """
        self._available_models = models
        self._model_combo.clear()
        self._model_combo.addItems(models)

    def get_settings(self) -> ConversationSettings:
        """Get the current settings from the dialog."""
        model = self._model_combo.currentText()
        temperature = self.temp_spin.value() if ConversationSettings.supports_temperature(model) else None
        return ConversationSettings(model=model, temperature=temperature)

    def set_settings(self, settings: ConversationSettings):
        """Set the current settings in the dialog."""
        self._initial_settings = ConversationSettings(
            model=settings.model,
            temperature=settings.temperature
        )
        self._current_settings = ConversationSettings(
            model=settings.model,
            temperature=settings.temperature
        )

        # Initialize temperature tracking for this dialog session
        self._model_temperatures = {
            settings.model: settings.temperature if settings.temperature is not None else 0.7
        }

        model_index = self._model_combo.findText(settings.model)
        if model_index >= 0:
            self._model_combo.setCurrentIndex(model_index)

        supports_temp = ConversationSettings.supports_temperature(settings.model)
        self.temp_spin.setEnabled(supports_temp)
        if supports_temp and settings.temperature is not None:
            self.temp_spin.setValue(settings.temperature)
        else:
            self.temp_spin.setValue(0.0)

        self.apply_button.setEnabled(False)

    def _handle_apply(self):
        """Handle Apply button click."""
        current_model = self._model_combo.currentText()
        if ConversationSettings.supports_temperature(current_model):
            self._model_temperatures[current_model] = self.temp_spin.value()

        settings = self.get_settings()
        self._current_settings = settings
        self.settings_changed.emit(settings)
        self.apply_button.setEnabled(False)

    def _handle_ok(self):
        """Handle OK button click."""
        self._handle_apply()
        self.accept()

    def reject(self):
        """Handle Cancel button click."""
        if self._initial_settings:
            self.settings_changed.emit(self._initial_settings)

        super().reject()
