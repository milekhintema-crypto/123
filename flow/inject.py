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


def secure_input_enabled_mac() -> bool:
    """macOS: включён ли «Безопасный ввод с клавиатуры» (Secure Input).

    Когда какая-либо программа (чаще всего Терминал: меню Терминал →
    «Безопасный ввод с клавиатуры») включает Secure Input, система
    молча блокирует ВСЕ синтетические нажатия клавиш — Cmd+V не дойдёт
    ни одним способом, при этом ошибок нигде не будет.
    """
    if sys.platform != "darwin":
        return False
    try:
        import ctypes

        carbon = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Carbon"
        )
        return bool(carbon.IsSecureEventInputEnabled())
    except Exception:
        return False


def macos_accessibility_trusted() -> bool:
    """True, если приложению выдано право «Универсальный доступ» (macOS).

    Без него эмуляция Cmd+V (любым способом) молча не срабатывает.
    """
    if sys.platform != "darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        # Не смогли проверить — не блокируем, просто вернём True
        return True


def _paste_mac_applescript(target_pid: int | None = None) -> bool:
    """macOS: Cmd+V через AppleScript / System Events — самый надёжный способ.

    Если задан target_pid, сначала выводим этот процесс на передний план
    (set frontmost) и только потом шлём Cmd+V — так вставка гарантированно
    попадает в целевое окно, даже если фокус ушёл в наше приложение.

    При первом вызове macOS покажет запрос «Терминал хочет управлять
    System Events» (раздел «Автоматизация») — нужно разрешить.
    """
    if target_pid is not None:
        script = (
            'tell application "System Events"\n'
            f"    set targetProc to first process whose unix id is {target_pid}\n"
            "    set frontmost of targetProc to true\n"
            "end tell\n"
            "delay 0.15\n"
            'tell application "System Events" to keystroke "v" using command down'
        )
    else:
        script = (
            'tell application "System Events" to keystroke "v" using command down'
        )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=6,
        )
        if result.returncode == 0:
            return True
        log.warning(
            "osascript paste failed (rc=%s): %s",
            result.returncode,
            result.stderr.decode("utf-8", "replace").strip(),
        )
        return False
    except Exception:
        log.exception("osascript paste failed")
        return False


def _paste_mac_quartz() -> None:
    """macOS fallback: Cmd+V через Quartz CGEvent.

    Полная последовательность (Cmd down → V down → V up → Cmd up)
    с микропаузами — «краткая» форма (только флаги) на новых macOS
    иногда молча игнорируется системой.
    """
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )

    V_KEYCODE = 9  # физическая клавиша «V»
    CMD_KEYCODE = 55  # левый Cmd

    cmd_down = CGEventCreateKeyboardEvent(None, CMD_KEYCODE, True)
    CGEventSetFlags(cmd_down, kCGEventFlagMaskCommand)
    v_down = CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
    CGEventSetFlags(v_down, kCGEventFlagMaskCommand)
    v_up = CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
    CGEventSetFlags(v_up, kCGEventFlagMaskCommand)
    cmd_up = CGEventCreateKeyboardEvent(None, CMD_KEYCODE, False)
    CGEventSetFlags(cmd_up, 0)

    for event in (cmd_down, v_down, v_up, cmd_up):
        CGEventPost(kCGHIDEventTap, event)
        time.sleep(0.01)


def _frontmost_app_mac() -> str:
    """Имя приложения в фокусе (диагностика: туда уйдёт Cmd+V)."""
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.localizedName() if app else "?"
    except Exception:
        return "?"


def frontmost_app_mac() -> tuple[int, str] | None:
    """(pid, имя) приложения в фокусе — целевое окно для вставки.

    Вызывается в момент НАЖАТИЯ хоткея, пока фокус ещё в целевом
    приложении (до показа overlay).
    """
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return int(app.processIdentifier()), str(app.localizedName())
    except Exception:
        log.exception("frontmost_app_mac failed")
        return None


def activate_app_mac(pid: int) -> bool:
    """Вернуть фокус приложению по pid перед вставкой."""
    try:
        from AppKit import (
            NSApplicationActivateIgnoringOtherApps,
            NSRunningApplication,
        )

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception:
        log.exception("activate_app_mac failed")
        return False


def _paste_mac_to_pid(pid: int) -> bool:
    """macOS: Cmd+V напрямую в процесс по pid через CGEventPostToPid.

    Событие доставляется конкретному приложению независимо от того, какое
    окно сейчас в фокусе — не нужно возвращать фокус и бороться с его
    кражей overlay'ем. Требует права «Универсальный доступ».
    """
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPostToPid,
            CGEventSetFlags,
            kCGEventFlagMaskCommand,
        )
    except Exception:
        log.exception("Quartz import failed")
        return False

    V_KEYCODE = 9
    CMD_KEYCODE = 55

    cmd_down = CGEventCreateKeyboardEvent(None, CMD_KEYCODE, True)
    CGEventSetFlags(cmd_down, kCGEventFlagMaskCommand)
    v_down = CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
    CGEventSetFlags(v_down, kCGEventFlagMaskCommand)
    v_up = CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
    CGEventSetFlags(v_up, kCGEventFlagMaskCommand)
    cmd_up = CGEventCreateKeyboardEvent(None, CMD_KEYCODE, False)
    CGEventSetFlags(cmd_up, 0)

    for event in (cmd_down, v_down, v_up, cmd_up):
        CGEventPostToPid(pid, event)
        time.sleep(0.01)
    return True


def _ensure_target_frontmost(target_pid: int, timeout: float = 1.2) -> bool:
    """Вернуть фокус целевому приложению и ДОЖДАТЬСЯ реального перехода.

    Прошлая версия спала фиксированные 0.3 c и не проверяла результат —
    для части приложений (Telegram) этого не хватало. Теперь:
    1) деактивируем себя (NSApp.deactivate — отдаёт фокус предыдущему),
    2) активируем цель по pid,
    3) опрашиваем frontmost, пока фокус фактически не перейдёт.
    """
    front = frontmost_app_mac()
    if front is not None and front[0] == target_pid:
        return True

    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().deactivate()
    except Exception:
        pass

    activate_app_mac(target_pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.05)
        front = frontmost_app_mac()
        if front is not None and front[0] == target_pid:
            return True
        # Повторяем активацию: первая попытка иногда «не берёт»
        activate_app_mac(target_pid)
    return False


def _paste_mac(target_pid: int | None = None) -> None:
    """macOS: вернуть фокус цели (с подтверждением), затем Cmd+V.

    Порядок: refocus-and-wait → AppleScript keystroke → Quartz CGEvent.
    """
    if secure_input_enabled_mac():
        # Событие всё равно отправим (вдруг блокировка снята частично),
        # но громко предупредим — это главный «невидимый» блокер вставки.
        log.error(
            "ВКЛЮЧЁН «Безопасный ввод с клавиатуры» (Secure Input) — macOS "
            "блокирует синтетический Cmd+V. Откройте меню «Терминал» → "
            "снимите галочку «Безопасный ввод с клавиатуры» (или закройте "
            "приложение, включившее Secure Input) и повторите."
        )
    if target_pid is not None:
        ok = _ensure_target_frontmost(target_pid)
        log.info(
            "Focus on target before paste: %s (frontmost=%s)",
            ok,
            _frontmost_app_mac(),
        )

    if _paste_mac_applescript(None):
        log.info("Paste sent via AppleScript/System Events")
        return
    log.info("Falling back to Quartz CGEvent paste")
    _paste_mac_quartz()
    log.info("Paste sent via Quartz CGEvent")


def _paste_keystroke() -> None:
    """Эмуляция сочетания «вставить» через pynput (Windows и fallback)."""
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

    def inject(self, text: str, target_pid: int | None = None) -> bool:
        """Вставить текст. Возвращает True при успехе.

        target_pid (macOS) — pid приложения, в котором был фокус в момент
        нажатия хоткея. Перед вставкой возвращаем ему фокус: даже если
        overlay/наше приложение украли фокус, Cmd+V попадёт куда нужно.
        """
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
            if sys.platform == "darwin":
                if not macos_accessibility_trusted():
                    # Право не выдано — Cmd+V не сработает. Текст оставляем
                    # в буфере (без восстановления), чтобы можно было
                    # вставить вручную, и явно сообщаем об ошибке.
                    log.error(
                        "macOS Accessibility (Универсальный доступ) не выдан — "
                        "автовставка невозможна. Текст оставлен в буфере обмена."
                    )
                    return False
                # target_pid: скрипт сам выведет целевое окно на передний
                # план перед вставкой
                _paste_mac(target_pid)
            elif sys.platform.startswith("linux"):
                if not _paste_linux():
                    _paste_keystroke()  # fallback (работает на X11)
            else:
                _paste_keystroke()
        except Exception:
            log.exception("Paste keystroke failed")
            return False

        # 4. Восстанавливаем буфер асинхронно и НЕ раньше чем через 10 с:
        #    если автовставка не сработала, у пользователя должно быть
        #    время вставить текст вручную (Cmd/Ctrl+V), прежде чем мы
        #    вернём старое содержимое буфера.
        if self._restore and old_clipboard is not None:

            def _restore_later(value: str) -> None:
                time.sleep(10.0)
                try:
                    pyperclip.copy(value)
                    log.debug("Clipboard restored to previous content")
                except Exception:
                    pass

            threading.Thread(
                target=_restore_later, args=(old_clipboard,), daemon=True
            ).start()

        return True
