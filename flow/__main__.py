"""Точка входа: python -m flow"""

from __future__ import annotations

import logging
import signal
import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .app import FlowController


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Flow Dictate")
    # Приложение живёт в трее: закрытие окна настроек не должно завершать его
    app.setQuitOnLastWindowClosed(False)

    if sys.platform == "darwin":
        # КРИТИЧНО для macOS: переводим приложение в режим «агента»
        # (accessory). Иначе показ overlay активирует наше приложение,
        # фокус уходит из целевого окна (курсор перестаёт мигать),
        # и Cmd+V вставляет текст не туда. Заодно убирает иконку из Dock.
        try:
            from AppKit import (
                NSApplication,
                NSApplicationActivationPolicyAccessory,
            )

            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to set accessory activation policy"
            )

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "Flow Dictate",
            "Системный трей недоступен. Приложение требует трея для работы.",
        )
        return 1

    controller = FlowController(app)
    controller.start()

    # Ctrl+C в терминале корректно завершает приложение
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
