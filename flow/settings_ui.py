"""Окно настроек (PySide6): хоткей, язык, YandexGPT, словарь, микрофон, ключ.

API-ключ показывается маскированным и сохраняется только в keyring.
Остальные настройки пишутся в YAML-конфиг.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from .audio import list_input_devices
from .config import Config
from .hotkey import AVAILABLE_HOTKEYS

LANGUAGES = {
    "ru-RU": "Русский",
    "en-US": "English (US)",
    "auto": "Автоопределение",
}

GPT_MODELS = ["yandexgpt-lite", "yandexgpt"]


class SettingsDialog(QDialog):
    """Диалог настроек. on_saved вызывается после сохранения конфига."""

    def __init__(self, config: Config, on_saved: Callable[[], None]) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Flow Dictate — настройки")
        self.setMinimumWidth(460)
        self._config = config
        self._on_saved = on_saved

        form = QFormLayout()

        # --- Хоткей ---
        self._hotkey_combo = QComboBox()
        for key_name, label in AVAILABLE_HOTKEYS.items():
            self._hotkey_combo.addItem(label, userData=key_name)
        idx = self._hotkey_combo.findData(config.hotkey)
        self._hotkey_combo.setCurrentIndex(max(idx, 0))
        form.addRow("Push-to-talk клавиша:", self._hotkey_combo)

        self._min_hold = QDoubleSpinBox()
        self._min_hold.setRange(0.0, 2.0)
        self._min_hold.setSingleStep(0.05)
        self._min_hold.setValue(config.min_hold_seconds)
        self._min_hold.setSuffix(" c")
        form.addRow("Мин. удержание:", self._min_hold)

        # --- Язык ---
        self._lang_combo = QComboBox()
        for code, label in LANGUAGES.items():
            self._lang_combo.addItem(label, userData=code)
        idx = self._lang_combo.findData(config.language)
        self._lang_combo.setCurrentIndex(max(idx, 0))
        form.addRow("Язык распознавания:", self._lang_combo)

        # --- Микрофон ---
        self._device_combo = QComboBox()
        self._device_combo.addItem("Системный по умолчанию", userData=None)
        for dev_idx, name in list_input_devices():
            self._device_combo.addItem(name, userData=dev_idx)
        if config.input_device is not None:
            idx = self._device_combo.findData(config.input_device)
            if idx >= 0:
                self._device_combo.setCurrentIndex(idx)
        form.addRow("Микрофон:", self._device_combo)

        # --- Yandex Cloud ---
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        self._api_key_edit.setPlaceholderText(
            "оставьте пустым, чтобы не менять" if Config.get_api_key() else "Api-Key…"
        )
        form.addRow("API-ключ Yandex Cloud:", self._api_key_edit)

        self._folder_edit = QLineEdit(config.folder_id)
        self._folder_edit.setPlaceholderText("b1g…")
        form.addRow("folder_id:", self._folder_edit)

        # --- Пост-обработка ---
        self._pp_check = QCheckBox("Очищать текст через YandexGPT")
        self._pp_check.setChecked(config.postprocess_enabled)
        form.addRow(self._pp_check)

        self._model_combo = QComboBox()
        self._model_combo.addItems(GPT_MODELS)
        idx = self._model_combo.findText(config.gpt_model)
        self._model_combo.setCurrentIndex(max(idx, 0))
        form.addRow("Модель YandexGPT:", self._model_combo)

        # --- Словарь ---
        self._glossary_edit = QPlainTextEdit("\n".join(config.glossary))
        self._glossary_edit.setPlaceholderText(
            "Термины, имена и аббревиатуры — по одному в строке.\n"
            "Передаются в SpeechKit как контекст и в промпт YandexGPT."
        )
        self._glossary_edit.setMaximumHeight(110)
        form.addRow("Словарь терминов:", self._glossary_edit)

        # --- Кнопки ---
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        hint = QLabel(
            "API-ключ хранится в системном хранилище секретов (keyring), "
            "не в файле конфигурации."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _save(self) -> None:
        cfg = self._config
        cfg.hotkey = self._hotkey_combo.currentData()
        cfg.min_hold_seconds = self._min_hold.value()
        cfg.language = self._lang_combo.currentData()
        cfg.input_device = self._device_combo.currentData()
        cfg.folder_id = self._folder_edit.text().strip()
        cfg.postprocess_enabled = self._pp_check.isChecked()
        cfg.gpt_model = self._model_combo.currentText()
        cfg.glossary = [
            line.strip()
            for line in self._glossary_edit.toPlainText().splitlines()
            if line.strip()
        ]
        cfg.save()

        new_key = self._api_key_edit.text().strip()
        if new_key:
            Config.set_api_key(new_key)

        self._on_saved()
        self.accept()
