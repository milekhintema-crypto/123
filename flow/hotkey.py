"""Глобальный push-to-talk хоткей на pynput.

Логика:
- Нажатие настроенной клавиши (по умолчанию right Option / alt_r)
  немедленно вызывает on_press_start() — запись стартует сразу,
  чтобы не потерять первые слова.
- Отпускание вызывает on_release(accepted):
    accepted=True  — клавишу держали дольше min_hold_seconds (диктовка),
    accepted=False — короткий тап (случайное нажатие / AltGr),
                     запись отменяется и ничего не вставляется.
Так обычное использование клавиши (например AltGr на Windows) не ломается:
короткие нажатия проходят в систему как обычно, мы их просто игнорируем.

pynput слушает клавиатуру пассивно (не перехватывает событие), поэтому
right Option продолжает работать как модификатор в других приложениях.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from pynput import keyboard

log = logging.getLogger(__name__)

# Человекочитаемые имена клавиш для настроек UI
AVAILABLE_HOTKEYS: dict[str, str] = {
    "alt_r": "Right Option / Right Alt",
    "alt_l": "Left Option / Left Alt",
    "ctrl_r": "Right Ctrl",
    "cmd_r": "Right Cmd / Right Win",
    "f8": "F8",
    "f9": "F9",
}


def _resolve_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    """Преобразовать строковое имя из конфига в объект pynput."""
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        # Одиночный печатный символ, например "§"
        return keyboard.KeyCode.from_char(name)


class HotkeyManager:
    """Слушатель глобальной push-to-talk клавиши.

    Колбэки вызываются из потока pynput — принимающая сторона обязана
    сама маршалить их в GUI-поток (в app.py это делают сигналы Qt).
    """

    def __init__(
        self,
        key_name: str,
        min_hold_seconds: float,
        on_press_start: Callable[[], None],
        on_release: Callable[[bool], None],
    ) -> None:
        self._key = _resolve_key(key_name)
        self._min_hold = min_hold_seconds
        self._on_press_start = on_press_start
        self._on_release = on_release

        self._pressed_at: float | None = None
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None
        self._enabled = True

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._handle_press, on_release=self._handle_release
        )
        self._listener.daemon = True
        self._listener.start()
        log.info("Hotkey listener started (key=%s)", self._key)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def set_enabled(self, enabled: bool) -> None:
        """Пауза/возобновление без остановки слушателя (пункт меню в трее)."""
        self._enabled = enabled

    def set_key(self, key_name: str) -> None:
        """Смена хоткея на лету из настроек."""
        self._key = _resolve_key(key_name)

    # ------------------------------------------------------------------
    def _matches(self, key: object) -> bool:
        return key == self._key

    def _handle_press(self, key: object) -> None:
        if not self._enabled or not self._matches(key):
            return
        with self._lock:
            # ОС повторяет события press при удержании — реагируем один раз
            if self._pressed_at is not None:
                return
            self._pressed_at = time.monotonic()
        try:
            self._on_press_start()
        except Exception:
            log.exception("on_press_start failed")

    def _handle_release(self, key: object) -> None:
        if not self._matches(key):
            return
        with self._lock:
            if self._pressed_at is None:
                return
            held = time.monotonic() - self._pressed_at
            self._pressed_at = None
        accepted = held >= self._min_hold
        try:
            self._on_release(accepted)
        except Exception:
            log.exception("on_release failed")
