"""Вставка текста в активное поле любого приложения.

Стратегия (та же, что у Wispr Flow): НЕ печатать посимвольно.
1. Сохранить текущий буфер обмена.
2. Положить текст в буфер.
3. Эмулировать платформенный paste:
   - macOS: Cmd+V (pynput; требует разрешения Accessibility)
   - Windows: Ctrl+V
   - Linux X11: xdotool (если установлен), иначе Ctrl+V через pynput
   - Linux Wayland: wtype / ydotool (если установлены), иначе Ctrl+V
4. Восстановить прежний буфер обмена (с небольшой задержкой,
   чтобы целевое приложение успело прочитать clipboard).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time

import pyperclip
from pynput.keyboard import Controller, Key

log = logging.getLogger(__name__)

_keyboard = Controller()


def _is_wayland() -> bool:
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def _paste_keystroke() -> None:
    """Эмуляция сочетания «вставить» через pynput."""
    modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
    with _keyboard.pressed(modifier):
        _keyboard.press("v")
        _keyboard.release("v")


def _paste_linux() -> bool:
    """Linux: пробуем нативные утилиты, они надёжнее pynput под Wayland."""
    if _is_wayland():
        if shutil.which("wtype"):
            # wtype умеет слать сочетания клавиш в Wayland
            subprocess.run(
                ["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"],
                check=False,
            )
            return True
        if shutil.which("ydotool"):
            # 29=ctrl, 47=v (коды evdev); требует запущенного ydotoold
            subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=False)
            return True
        return False
    # X11
    if shutil.which("xdotool"):
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=False)
        return True
    return False


class TextInjector:
    """Вставляет текст в активное окно через clipboard + paste."""

    def __init__(self, paste_delay: float = 0.08, restore_clipboard: bool = True) -> None:
        self._paste_delay = paste_delay
        self._restore = restore_clipboard

    def inject(self, text: str) -> bool:
        """Вставить текст. Возвращает True при успехе."""
        if not text:
            return False

        # 1. Сохраняем старый clipboard (может бросить, если пусто/не текст)
        old_clipboard: str | None = None
        if self._restore:
            try:
                old_clipboard = pyperclip.paste()
            except Exception:
                old_clipboard = None

        # 2. Кладём наш текст
        try:
            pyperclip.copy(text)
        except Exception:
            log.exception("Failed to set clipboard")
            return False

        # Даём clipboard-менеджеру время принять данные
        time.sleep(self._paste_delay)

        # 3. Эмулируем paste
        try:
            if sys.platform.startswith("linux"):
                if not _paste_linux():
                    _paste_keystroke()  # fallback (работает на X11)
            else:
                _paste_keystroke()
        except Exception:
            log.exception("Paste keystroke failed")
            return False

        # 4. Восстанавливаем буфер асинхронно, дав приложению время
        #    прочитать наш текст из clipboard.
        if self._restore and old_clipboard is not None:

            def _restore_later(value: str) -> None:
                time.sleep(1.0)
                try:
                    pyperclip.copy(value)
                except Exception:
                    pass

            threading.Thread(
                target=_restore_later, args=(old_clipboard,), daemon=True
            ).start()

        return True
