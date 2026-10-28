from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from librivox_trimmer.waveform import FrameGrid, WaveformWindow, column_peaks


class WaveformView(QWidget):
    """Short waveform with a frame-snapped cut marker that can be clicked, dragged, or nudged."""

    cut_changed = Signal(float)

    RULER_HEIGHT = 18
    MARGIN = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self._window: WaveformWindow | None = None
        self._grid: FrameGrid | None = None
        self._peaks: list[tuple[int, int]] = []
        self._peak_width = 0
        self._cut: float | None = None
        self._starts_kept_audio = True
        self._playhead: float | None = None
        self._message = "Select a chapter to see its waveform"

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(120)
        return size

    @property
    def window_range(self) -> tuple[float, float] | None:
        return (self._window.start, self._window.end) if self._window else None

    @property
    def cut(self) -> float | None:
        return self._cut

    def set_message(self, message: str) -> None:
        self._window = None
        self._peaks = []
        self._message = message
        self.update()

    def set_waveform(self, window: WaveformWindow, grid: FrameGrid | None) -> None:
        self._window = window
        self._grid = grid
        self._peaks = []
        self._message = ""
        self.update()

    def set_cut(self, time: float | None, *, starts_kept_audio: bool) -> None:
        self._cut = time
        self._starts_kept_audio = starts_kept_audio
        self.update()

    def set_playhead(self, time: float | None) -> None:
        if time != self._playhead:
            self._playhead = time
            self.update()

    def plot_rect(self) -> QRectF:
        return QRectF(
            self.MARGIN, self.MARGIN,
            max(1.0, self.width() - 2 * self.MARGIN),
            max(1.0, self.height() - 2 * self.MARGIN - self.RULER_HEIGHT),
        )

    def time_at(self, x: float) -> float | None:
        if self._window is None:
            return None
        rect = self.plot_rect()
        fraction = min(1.0, max(0.0, (x - rect.left()) / rect.width()))
        return self._window.start + fraction * (self._window.end - self._window.start)

    def x_at(self, time: float) -> float | None:
        if self._window is None:
            return None
        rect = self.plot_rect()
        span = self._window.end - self._window.start
        return rect.left() + (time - self._window.start) / span * rect.width()

    def snap(self, time: float) -> float:
        if self._grid is None:
            return round(time, 3)
        boundary = self._grid.nearest_boundary(time)
        start, end = self.window_range or (time, time)
        if boundary < start:
            boundary += self._grid.frame_seconds
        elif boundary > end:
            boundary -= self._grid.frame_seconds
        return boundary

    def _choose(self, time: float | None) -> None:
        if time is None:
            return
        self._cut = self.snap(time)
        self.update()
        self.cut_changed.emit(self._cut)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._window is not None:
            self.setFocus()
            self._choose(self.time_at(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._window is not None:
            self._choose(self.time_at(event.position().x()))
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._window is None or event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            super().keyPressEvent(event)
            return
        step = self._grid.frame_seconds if self._grid else 0.01
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            step *= 10
        direction = -1 if event.key() == Qt.Key.Key_Left else 1
        start, end = self.window_range
        current = self._cut if self._cut is not None else (start + end) / 2
        self._choose(min(end, max(start, current + direction * step)))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.plot_rect()
        painter.fillRect(self.rect(), QColor("#f7f9fa"))
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#d0d6db")))
        painter.drawRect(rect)
        if self._window is None:
            painter.setPen(QColor("#697386"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._message)
            return

        columns = max(1, int(rect.width()))
        if columns != self._peak_width or not self._peaks:
            self._peaks = column_peaks(self._window.samples, columns)
            self._peak_width = columns
        middle = rect.center().y()
        scale = rect.height() / 2 / 32768

        if self._cut is not None:
            x = self.x_at(self._cut)
            removed = (
                QRectF(rect.left(), rect.top(), max(0.0, x - rect.left()), rect.height())
                if self._starts_kept_audio
                else QRectF(x, rect.top(), max(0.0, rect.right() - x), rect.height())
            ).intersected(rect)
            painter.fillRect(removed, QColor(214, 69, 65, 40))

        painter.setPen(QPen(QColor("#176b62")))
        for column, (low, high) in enumerate(self._peaks):
            x = rect.left() + column
            painter.drawLine(QPointF(x, middle - high * scale), QPointF(x, middle - low * scale))

        self._paint_ruler(painter, rect)
        if self._playhead is not None and self._window.start <= self._playhead <= self._window.end:
            painter.setPen(QPen(QColor("#2f6fdd"), 1))
            x = self.x_at(self._playhead)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if self._cut is not None and self._window.start <= self._cut <= self._window.end:
            pen = QPen(QColor("#c0392b"), 2)
            painter.setPen(pen)
            x = self.x_at(self._cut)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

    def _paint_ruler(self, painter: QPainter, rect: QRectF) -> None:
        start, end = self.window_range
        painter.setPen(QPen(QColor("#697386")))
        tick = 0.5
        first = int(start / tick) + (0 if start % tick == 0 else 1)
        index = first
        while index * tick <= end:
            time = index * tick
            x = self.x_at(time)
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x, rect.bottom() + 4))
            if index % 2 == 0:
                painter.drawText(QPointF(x + 2, rect.bottom() + self.RULER_HEIGHT - 4), f"{time:.1f}s")
            index += 1
