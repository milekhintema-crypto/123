"""Конфигурация приложения: YAML-файл для настроек + keyring для секретов.

Настройки хранятся в ~/.config/flow-dictate/config.yaml (или платформенный аналог).
API-ключ Yandex Cloud НИКОГДА не пишется в конфиг — только в системное
хранилище секретов через keyring (macOS Keychain / Windows Credential
Locker / Secret Service на Linux).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import keyring
import yaml

from . import APP_NAME

# Имя записи в системном хранилище секретов
_KEYRING_SERVICE = APP_NAME
_KEYRING_API_KEY = "yandex-api-key"


def config_dir() -> Path:
    """Платформенная директория конфигурации."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.yaml"


@dataclass
class Config:
    """Все несекретные настройки приложения."""

    # --- Хоткей ---
    # Имя клавиши pynput: alt_r = right Option (macOS) / right Alt (Win/Linux)
    hotkey: str = "alt_r"
    # Нажатие короче этого порога (сек) считается случайным и игнорируется,
    # чтобы не ломать обычное использование клавиши (например, AltGr).
    min_hold_seconds: float = 0.25

    # --- Распознавание (Yandex SpeechKit v3) ---
    language: str = "ru-RU"
    stt_endpoint: str = "stt.api.cloud.yandex.net:443"
    sample_rate: int = 16000
    # Пользовательский словарь: термины, имена, аббревиатуры.
    glossary: list[str] = field(default_factory=list)

    # --- Пост-обработка (YandexGPT) ---
    postprocess_enabled: bool = True
    folder_id: str = ""
    # yandexgpt | yandexgpt-lite
    gpt_model: str = "yandexgpt-lite"
    gpt_endpoint: str = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    gpt_temperature: float = 0.1
    gpt_timeout_seconds: float = 15.0

    # --- Аудио ---
    # Индекс устройства sounddevice; None = устройство по умолчанию.
    input_device: int | None = None
    # Агрессивность VAD 0..3 (0 — мягко, 3 — жёстко). Используется для
    # индикации речи в overlay; в SpeechKit шлём всё аудио без обрезки.
    vad_aggressiveness: int = 2

    # --- Вставка текста ---
    # Задержка (сек) между установкой clipboard и эмуляцией paste.
    paste_delay_seconds: float = 0.08
    # Восстанавливать ли прежнее содержимое буфера обмена после вставки.
    restore_clipboard: bool = True

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        """Загрузить конфиг с диска; отсутствующие поля берут дефолты."""
        path = config_path()
        if not path.exists():
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(asdict(self), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    # --- Секреты (keyring) ------------------------------------------------
    @staticmethod
    def get_api_key() -> str | None:
        """API-ключ Yandex Cloud (общий для SpeechKit и YandexGPT)."""
        try:
            return keyring.get_password(_KEYRING_SERVICE, _KEYRING_API_KEY)
        except Exception:
            # Fallback для headless-окружений без keyring-бэкенда
            return os.environ.get("YC_API_KEY")

    @staticmethod
    def set_api_key(value: str) -> None:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_API_KEY, value)
