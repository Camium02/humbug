"""Dialog for configuring conversation-specific settings."""

from typing import List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDoubleSpinBox
)
from PySide6.QtCore import QSize, Signal

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
        self.setFixedSize(QSize(400, 200))
        self.setModal(True)

        self._available_models: List[str] = []
        self._initial_settings = None
        self._current_settings = None
        self._model_temperatures = {}

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        # Model selection
        model_layout = QHBoxLayout()
        model_label = QLabel("AI Model:")
        self._model_combo = QComboBox()
        self._model_combo.currentTextChanged.connect(self._handle_model_change)
        model_layout.addWidget(model_label)
        model_layout.addStretch()
        model_layout.addWidget(self._model_combo)
        layout.addLayout(model_layout)

        # Temperature setting
        temp_layout = QHBoxLayout()
        temp_label = QLabel("Temperature:")
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setDecimals(1)
        temp_layout.addWidget(temp_label)
        temp_layout.addStretch()
        temp_layout.addWidget(self.temp_spin)
        layout.addLayout(temp_layout)

        layout.addStretch()

        # Button row
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")
        self.apply_button = QPushButton("Apply")

        self.ok_button.clicked.connect(self._handle_ok)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self._handle_apply)

        self._model_combo.currentTextChanged.connect(self._handle_value_change)
        self.temp_spin.valueChanged.connect(self._handle_value_change)

        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.apply_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

        style_manager = StyleManager()
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {style_manager.get_color_str(ColorRole.BACKGROUND_SECONDARY)};
                color: white;
            }}
            QLabel {{
                color: white;
            }}
            QComboBox {{
                width: 200px;
            }}
            QDoubleSpinBox {{
                width: 200px;
            }}
            QPushButton {{
                background-color: #4d4d4d;
                color: white;
                border: none;
                border-radius: 2px;
                padding: 10px 15px;
                min-width: 70px;
            }}
            QPushButton:hover {{
                background-color: #5d5d5d;
            }}
            QPushButton:pressed {{
                background-color: #3d3d3d;
            }}
            QPushButton:disabled {{
                background-color: {style_manager.get_color_str(ColorRole.BACKGROUND_SECONDARY)};
                color: #808080;
            }}
        """)

    def _handle_model_change(self, model: str):
        """Handle model selection changes."""
        supports_temp = ConversationSettings.supports_temperature(model)
        self.temp_spin.setEnabled(supports_temp)

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
        """Set the list of available models.

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
