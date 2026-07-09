"""Плавающий overlay: frameless always-on-top окно у курсора.

Показывает пульсирующий индикатор микрофона и живой распознаваемый текст
(партиалы из SpeechKit). Кликов не перехватывает (transparent for input),
чтобы не мешать работе с активным приложением.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, Property
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

log = logging.getLogger(__name__)


class _PulsingDot(QWidget):
    """Пульсирующая красная точка — индикатор записи."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self._scale = 1.0
        # Бесконечная анимация «дыхания»
        self._anim = QPropertyAnimation(self, b"scale", self)
        self._anim.setDuration(900)
        self._anim.setStartValue(0.55)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._processing = False

    def get_scale(self) -> float:
        return self._scale

    def set_scale(self, value: float) -> None:
        self._scale = value
        self.update()

    scale = Property(float, get_scale, set_scale)

    def start(self) -> None:
        self._processing = False
        self._anim.start()

    def stop(self) -> None:
        self._anim.stop()

    def set_processing(self, processing: bool) -> None:
        """Жёлтый цвет — идёт пост-обработка YandexGPT."""
        self._processing = processing
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#f5a623") if self._processing else QColor("#e74c3c")
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        radius = 8 * self._scale
        center = self.rect().center()
        painter.drawEllipse(center, radius, radius)


class Overlay(QWidget):
    """Окно живой диктовки. Методы вызывать только из GUI-потока."""

    MAX_TEXT_CHARS = 220  # показываем хвост, чтобы окно не разрасталось

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Не перехватываем мышь — окно чисто информационное
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._dot = _PulsingDot(self)
        self._label = QLabel("Слушаю…", self)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "color: white; font-size: 14px; font-weight: 500;"
        )
        self._label.setMaximumWidth(420)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(10)
        layout.addWidget(self._dot, alignment=Qt.AlignTop)
        layout.addWidget(self._label)

        # Автоскрытие после вставки
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_overlay)

        # macOS: патч нативного окна выполняется один раз при первом показе
        self._mac_panel_patched = False

    # ------------------------------------------------------------------
    def _apply_macos_nonactivating(self) -> None:
        """macOS: сделать окно неактивирующейся панелью.

        Qt создаёт для Qt.Tool-окон NSPanel; добавляем ему стиль
        NonactivatingPanel — тогда показ overlay НЕ активирует наше
        приложение и фокус (мигающий курсор) остаётся в целевом окне.
        Именно так работают Spotlight-подобные утилиты.
        """
        if self._mac_panel_patched or sys.platform != "darwin":
            return
        self._mac_panel_patched = True
        try:
            from ctypes import c_void_p

            import objc
            from AppKit import NSPanel

            NS_NONACTIVATING_PANEL_MASK = 1 << 7  # NSWindowStyleMaskNonactivatingPanel

            nsview = objc.objc_object(c_void_p=int(self.winId()))
            nswindow = nsview.window()
            if nswindow is not None and nswindow.isKindOfClass_(NSPanel):
                nswindow.setStyleMask_(
                    int(nswindow.styleMask()) | NS_NONACTIVATING_PANEL_MASK
                )
                nswindow.setBecomesKeyOnlyIfNeeded_(True)
                log.info("Overlay patched to non-activating NSPanel")
            else:
                log.warning(
                    "Overlay native window is not NSPanel (%s) — cannot patch",
                    type(nswindow).__name__ if nswindow is not None else None,
                )
        except Exception:
            log.exception("Failed to patch overlay into non-activating panel")

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        """Полупрозрачная тёмная подложка со скруглёнными углами."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(20, 20, 24, 225))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), 14, 14)

    # ------------------------------------------------------------------
    def show_listening(self) -> None:
        """Начало записи: показать окно у курсора с пустым текстом."""
        self._hide_timer.stop()
        self._dot.set_processing(False)
        self._dot.start()
        self._label.setText("Слушаю…")
        self._reposition()
        self.show()
        # Патчим нативное окно после первого show(), когда оно уже создано;
        # raise_() не вызываем — на macOS он может активировать приложение.
        self._apply_macos_nonactivating()

    def set_partial_text(self, text: str) -> None:
        """Живой текст из ASR (обрезаем начало, если слишком длинно)."""
        if len(text) > self.MAX_TEXT_CHARS:
            text = "…" + text[-self.MAX_TEXT_CHARS :]
        self._label.setText(text or "Слушаю…")
        self.adjustSize()

    def show_processing(self) -> None:
        """Отпустили клавишу: ждём финал + YandexGPT."""
        self._dot.set_processing(True)
        current = self._label.text()
        if current in ("", "Слушаю…"):
            self._label.setText("Обрабатываю…")

    def show_error(self, message: str) -> None:
        self._dot.stop()
        self._label.setText(f"⚠ {message}")
        self.adjustSize()
        self._hide_timer.start(3500)

    def hide_overlay(self) -> None:
        self._dot.stop()
        self.hide()

    def flash_done(self) -> None:
        """Текст вставлен — коротко показать и скрыть."""
        self._dot.stop()
        self._hide_timer.start(250)

    # ------------------------------------------------------------------
    def _reposition(self) -> None:
        """Разместить окно чуть ниже курсора, не вылезая за экран."""
        self.adjustSize()
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(pos.x() + 16, geo.left()), geo.right() - self.width())
        y = min(max(pos.y() + 24, geo.top()), geo.bottom() - self.height())
        self.move(x, y)
