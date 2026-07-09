"""Иконка в системном трее: статус, вкл/выкл, настройки, выход.

Иконка рисуется программно (микрофон в круге), чтобы не тянуть ресурсы.
Цвет отражает состояние: серый — выключено, белый — готов,
красный — идёт запись, жёлтый — обработка.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def _make_icon(color: str) -> QIcon:
    """Нарисовать простую иконку микрофона заданного цвета."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    painter.setPen(Qt.NoPen)
    painter.setBrush(c)
    # Капсула микрофона
    painter.drawRoundedRect(QRect(24, 8, 16, 28), 8, 8)
    # Дуга-держатель
    pen = painter.pen()
    from PySide6.QtGui import QPen

    pen = QPen(c, 5)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRect(16, 16, 32, 30), 200 * 16, 140 * 16)
    # Ножка
    painter.drawLine(32, 46, 32, 54)
    painter.drawLine(24, 54, 40, 54)
    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    """Системный трей приложения."""

    ICON_COLORS = {
        "idle": "#e8e8e8",
        "recording": "#e74c3c",
        "processing": "#f5a623",
        "disabled": "#7a7a7a",
    }

    def __init__(
        self,
        on_toggle_enabled: Callable[[bool], None],
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        super().__init__(_make_icon(self.ICON_COLORS["idle"]))
        self.setToolTip("Flow Dictate — зажмите right Option и говорите")

        menu = QMenu()

        self._status_action = QAction("Готов к диктовке")
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()

        self._enabled_action = QAction("Включено")
        self._enabled_action.setCheckable(True)
        self._enabled_action.setChecked(True)
        self._enabled_action.toggled.connect(on_toggle_enabled)
        menu.addAction(self._enabled_action)

        settings_action = QAction("Настройки…")
        settings_action.triggered.connect(on_open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("Выход")
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)

        # Держим ссылки, чтобы Qt не собрал объекты
        self._menu = menu
        self._actions = [self._status_action, self._enabled_action, settings_action, quit_action]
        self.setContextMenu(menu)

    # ------------------------------------------------------------------
    def set_state(self, state: str, status_text: str) -> None:
        """state: idle | recording | processing | disabled."""
        self.setIcon(_make_icon(self.ICON_COLORS.get(state, "#e8e8e8")))
        self._status_action.setText(status_text)
