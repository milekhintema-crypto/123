"""Главный контроллер: связывает hotkey → audio → asr → postprocess → inject.

Поток данных одной диктовки:

  press right Option
      → AudioCapture.start()          (аудио-чанки в очередь)
      → SpeechKitStreamer.start(q)    (gRPC-стрим, партиалы → overlay)
      → Overlay.show_listening()
  release
      → AudioCapture.stop()           (сентинель None закрывает стрим)
      → SpeechKit отдаёт финал
      → PostProcessor.clean()         (в фоновом потоке, если включено)
      → TextInjector.inject()         (clipboard + Cmd/Ctrl+V)
      → Overlay скрывается

Все колбэки из фоновых потоков (pynput, gRPC, аудио) маршалятся в
GUI-поток через сигналы Qt — виджеты трогаем только из GUI-потока.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from .asr import SpeechKitStreamer
from .audio import AudioCapture
from .config import Config
from .hotkey import HotkeyManager
from .inject import TextInjector
from .overlay import Overlay
from .postprocess import PostProcessor
from .settings_ui import SettingsDialog
from .tray import Tray

log = logging.getLogger(__name__)


class FlowController(QObject):
    """Оркестратор всего приложения. Живёт в GUI-потоке."""

    # Сигналы для маршалинга событий из фоновых потоков в GUI-поток
    _sig_recording_started = Signal()
    _sig_recording_released = Signal(bool)  # accepted
    _sig_partial = Signal(str)
    _sig_final = Signal(str)
    _sig_error = Signal(str)
    _sig_inject_ready = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._config = Config.load()

        # --- Компоненты -------------------------------------------------
        self._overlay = Overlay()
        self._tray = Tray(
            on_toggle_enabled=self._on_toggle_enabled,
            on_open_settings=self._open_settings,
            on_quit=self._quit,
        )
        self._injector = TextInjector(
            paste_delay=self._config.paste_delay_seconds,
            restore_clipboard=self._config.restore_clipboard,
        )
        self._audio = AudioCapture(
            device=self._config.input_device,
            vad_aggressiveness=self._config.vad_aggressiveness,
        )
        self._hotkey = HotkeyManager(
            key_name=self._config.hotkey,
            min_hold_seconds=self._config.min_hold_seconds,
            on_press_start=self._sig_recording_started.emit,
            on_release=self._sig_recording_released.emit,
        )

        self._streamer: SpeechKitStreamer | None = None
        self._recording = False
        self._settings_dialog: SettingsDialog | None = None

        # --- Сигналы → слоты (исполняются в GUI-потоке) -------------------
        self._sig_recording_started.connect(self._start_recording)
        self._sig_recording_released.connect(self._stop_recording)
        self._sig_partial.connect(self._overlay.set_partial_text)
        self._sig_final.connect(self._handle_final)
        self._sig_error.connect(self._handle_error)
        self._sig_inject_ready.connect(self._inject_text)

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._tray.show()
        self._hotkey.start()
        self._tray.set_state("idle", "Готов к диктовке")

        # Проверяем право «Универсальный доступ» — без него автовставка не
        # работает (текст лишь копируется в буфер).
        from .inject import macos_accessibility_trusted

        if not macos_accessibility_trusted():
            log.warning("macOS Accessibility (Универсальный доступ) НЕ выдан")
            QMessageBox.warning(
                None,
                "Flow Dictate — нужно разрешение",
                "Не выдан «Универсальный доступ» (Accessibility).\n\n"
                "Без него распознанный текст НЕ будет вставляться "
                "автоматически (только копироваться в буфер).\n\n"
                "Откройте: Системные настройки → Конфиденциальность и "
                "безопасность → Универсальный доступ → включите «Терминал», "
                "затем ПЕРЕЗАПУСТИТЕ программу.",
            )
        else:
            log.info("macOS Accessibility: OK")

        if not Config.get_api_key():
            # Первый запуск: сразу открываем настройки для ввода ключа
            self._first_run_notice()
            self._open_settings()

    def _first_run_notice(self) -> None:
        import sys

        extra = ""
        if sys.platform == "darwin":
            extra = (
                "\n\nmacOS: выдайте приложению разрешения в Системных "
                "настройках → Конфиденциальность и безопасность:\n"
                "• Микрофон\n• Универсальный доступ (Accessibility)\n"
                "• Мониторинг ввода (Input Monitoring)"
            )
        QMessageBox.information(
            None,
            "Flow Dictate — первый запуск",
            "Укажите API-ключ Yandex Cloud и folder_id в настройках.\n"
            "Один ключ используется для SpeechKit (распознавание) и "
            "YandexGPT (очистка текста)." + extra,
        )

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------
    def _start_recording(self) -> None:
        if self._recording:
            return
        api_key = Config.get_api_key()
        if not api_key:
            self._tray.set_state("idle", "Нет API-ключа — откройте настройки")
            return

        self._recording = True
        self._tray.set_state("recording", "Идёт запись…")
        self._overlay.show_listening()

        # Свежая сессия ASR на каждую диктовку
        self._streamer = SpeechKitStreamer(
            api_key=api_key,
            endpoint=self._config.stt_endpoint,
            language=self._config.language,
            sample_rate=self._config.sample_rate,
            glossary=self._config.glossary,
            on_partial=self._sig_partial.emit,
            on_final=self._sig_final.emit,
            on_error=self._sig_error.emit,
        )
        try:
            audio_queue = self._audio.start()
        except Exception as exc:  # микрофон занят / нет прав
            log.exception("Failed to start audio")
            self._recording = False
            self._overlay.show_error(f"Микрофон: {exc}")
            self._tray.set_state("idle", "Ошибка микрофона")
            return
        self._streamer.start(audio_queue)

    def _stop_recording(self, accepted: bool) -> None:
        if not self._recording:
            return
        self._recording = False
        self._audio.stop()  # кладёт None → gRPC-стрим корректно закрывается

        if not accepted:
            # Короткий тап — отменяем без вставки
            if self._streamer:
                self._streamer.cancel()
            self._overlay.hide_overlay()
            self._tray.set_state("idle", "Готов к диктовке")
            return

        self._overlay.show_processing()
        self._tray.set_state("processing", "Распознаю…")
        # Дальше ждём _handle_final от ASR-потока

    # ------------------------------------------------------------------
    # Финал → пост-обработка → вставка
    # ------------------------------------------------------------------
    def _handle_final(self, text: str) -> None:
        log.info("Final transcript received (%d chars): %r", len(text), text)
        if not text:
            log.warning("Empty final transcript — nothing to inject")
            self._overlay.hide_overlay()
            self._tray.set_state("idle", "Готов к диктовке")
            return

        if self._config.postprocess_enabled and self._config.folder_id:
            self._tray.set_state("processing", "Очищаю текст (YandexGPT)…")
            api_key = Config.get_api_key() or ""
            processor = PostProcessor(
                api_key=api_key,
                folder_id=self._config.folder_id,
                model=self._config.gpt_model,
                endpoint=self._config.gpt_endpoint,
                temperature=self._config.gpt_temperature,
                timeout=self._config.gpt_timeout_seconds,
                glossary=self._config.glossary,
            )

            def _worker() -> None:
                cleaned = processor.clean(text)
                self._sig_inject_ready.emit(cleaned)

            threading.Thread(target=_worker, daemon=True, name="postprocess").start()
        else:
            self._inject_text(text)

    def _inject_text(self, text: str) -> None:
        log.info("Injecting text (%d chars): %r", len(text), text)
        ok = self._injector.inject(text)
        log.info("Injection result: %s (текст также помещён в буфер обмена)", ok)
        if ok:
            self._overlay.flash_done()
            self._tray.set_state("idle", "Готов к диктовке")
        else:
            self._overlay.show_error("Не удалось вставить текст")
            self._tray.set_state("idle", "Ошибка вставки")

    def _handle_error(self, message: str) -> None:
        self._overlay.show_error(message)
        self._tray.set_state("idle", "Ошибка — см. overlay")

    # ------------------------------------------------------------------
    # Трей / настройки / выход
    # ------------------------------------------------------------------
    def _on_toggle_enabled(self, enabled: bool) -> None:
        self._hotkey.set_enabled(enabled)
        state = "idle" if enabled else "disabled"
        text = "Готов к диктовке" if enabled else "Выключено"
        self._tray.set_state(state, text)

    def _open_settings(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            return
        self._settings_dialog = SettingsDialog(self._config, self._apply_settings)
        self._settings_dialog.finished.connect(self._settings_closed)
        self._settings_dialog.show()

    def _settings_closed(self, *_: object) -> None:
        self._settings_dialog = None

    def _apply_settings(self) -> None:
        """Применить изменённые настройки без перезапуска."""
        self._hotkey.set_key(self._config.hotkey)
        self._audio.set_device(self._config.input_device)
        self._injector = TextInjector(
            paste_delay=self._config.paste_delay_seconds,
            restore_clipboard=self._config.restore_clipboard,
        )
        log.info("Settings applied")

    def _quit(self) -> None:
        self._hotkey.stop()
        if self._recording:
            self._audio.stop()
        self._tray.hide()
        self._app.quit()
