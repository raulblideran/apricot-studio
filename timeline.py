"""The timeline: filmstrip, waveform, keyframe ticks, in/out handles, playhead.

The static bands are rendered once into a pixmap and re-blitted, so moving the
playhead during 60fps playback costs almost nothing. The pixmap is rebuilt only
when the data, the size or the visible time range changes.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

import theme
from media import THUMB_H, THUMB_W, fmt_tc

FILM_H = 58
WAVE_H = 34
RULER_H = 20
PAD = 6                     # so handles at 0 and at the end stay fully visible
HANDLE_W = 9
GRAB_PX = 10                # how close the pointer must be to grab a handle
SNAP_PX = 7                 # in-point snaps to a keyframe within this many pixels
MIN_SPAN = 0.25             # don't zoom in past a quarter second

BG = QColor(28, 30, 34)
FILM_BG = QColor(20, 22, 25)
WAVE_BG = QColor(24, 26, 30)
WAVE_FG = QColor(96, 170, 255)
RULER_BG = QColor(22, 24, 28)
RULER_FG = QColor(150, 156, 168)
KEYFRAME = QColor(88, 96, 112)
KEYFRAME_HOT = QColor(120, 220, 150)
ACCENT = QColor(theme.DEFAULT_ACCENT)          # replaced per instance
DIM = QColor(10, 11, 13, 168)
PLAYHEAD = QColor(255, 255, 255)
POPUP_BG = QColor(16, 18, 21, 245)
POPUP_EDGE = QColor(70, 76, 88)

# Label steps that read naturally; the first one giving <= ~9 labels wins.
_STEPS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)


def _short_tc(seconds: float, fine: bool) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    frac = f".{int(round((seconds - total) * 10))}" if fine else ""
    return (f"{h}:{m:02d}:{s:02d}{frac}" if h else f"{m}:{s:02d}{frac}")


class Timeline(QWidget):
    seeked = pyqtSignal(float)
    inChanged = pyqtSignal(float)
    outChanged = pyqtSignal(float)
    scrubbing = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(FILM_H + WAVE_H + RULER_H)
        self.setMouseTracking(True)
        # Takes focus on click so that clicking the timeline releases a text
        # field, but never handles keys itself -- they propagate to the window.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self._duration = 0.0
        self._position = 0.0
        self._in = 0.0
        self._out = 0.0
        self._thumbs: list[QImage] = []
        self._peaks: list[float] = []
        self._keyframes: list[float] = []
        self._cache: QPixmap | None = None
        self._drag: str | None = None
        # Follows the user's accent; the grip colour is derived so the
        # handle stays legible whatever colour is chosen.
        self._accent = QColor(theme.DEFAULT_ACCENT)
        self._grip = QColor(theme.on_accent(theme.DEFAULT_ACCENT))
        self._snapped = False

        # Visible range. Starts as the whole file.
        self._view_start = 0.0
        self._view_end = 0.0
        self._hover_x: float | None = None

    def set_accent(self, colour: str) -> None:
        colour = theme.normalise(colour)
        self._accent = QColor(colour)
        self._grip = QColor(theme.on_accent(colour))
        self.update()

    # ----- data ---------------------------------------------------------

    def reset(self, duration: float) -> None:
        self._duration = max(duration, 0.0)
        self._position = 0.0
        self._in = 0.0
        self._out = self._duration
        self._thumbs = []
        self._peaks = []
        self._keyframes = []
        self._view_start = 0.0
        self._view_end = self._duration
        self._hover_x = None
        self._invalidate()

    def add_thumbnail(self, image: QImage) -> None:
        self._thumbs.append(image)
        self._invalidate()

    def set_peaks(self, peaks: list[float]) -> None:
        self._peaks = peaks
        self._invalidate()

    def set_keyframes(self, times: list[float]) -> None:
        self._keyframes = times
        self._invalidate()

    @property
    def in_point(self) -> float:
        return self._in

    @property
    def out_point(self) -> float:
        return self._out

    @property
    def snapped(self) -> bool:
        """Whether the in-point currently sits on a keyframe."""
        return self._snapped

    def set_in(self, value: float, snapped: bool | None = None) -> None:
        self._in = min(max(value, 0.0), self._duration)
        self._out = max(self._out, self._in)
        if snapped is not None:
            self._snapped = snapped
        else:
            self._snapped = any(abs(k - self._in) <= 0.002 for k in self._keyframes)
        self.update()

    def set_out(self, value: float) -> None:
        self._out = min(max(value, 0.0), self._duration)
        self._in = min(self._in, self._out)
        self.update()

    def set_position(self, value: float) -> None:
        self._position = min(max(value, 0.0), self._duration)
        self.update()

    def _invalidate(self) -> None:
        self._cache = None
        self.update()

    # ----- zoom ---------------------------------------------------------

    @property
    def view_span(self) -> float:
        return max(self._view_end - self._view_start, MIN_SPAN)

    @property
    def zoomed(self) -> bool:
        return self._duration > 0 and self.view_span < self._duration - 1e-6

    def zoom_to_selection(self) -> None:
        """Frame the current in/out with a little air on each side."""
        if self._duration <= 0:
            return
        span = max(self._out - self._in, MIN_SPAN)
        pad = span * 0.15
        self._set_view(self._in - pad, self._out + pad)

    def zoom_all(self) -> None:
        self._set_view(0.0, self._duration)

    def zoom_in(self) -> None:
        self._zoom_at(0.6, self._position)

    def zoom_out(self) -> None:
        self._zoom_at(1.0 / 0.6, self._position)

    def _set_view(self, start: float, end: float) -> None:
        span = min(max(end - start, MIN_SPAN), self._duration or MIN_SPAN)
        start = min(max(start, 0.0), max(self._duration - span, 0.0))
        self._view_start, self._view_end = start, start + span
        self._invalidate()

    def _zoom_at(self, factor: float, anchor_time: float) -> None:
        """Zoom keeping `anchor_time` under the same pixel."""
        span = self.view_span
        new_span = min(max(span * factor, MIN_SPAN), self._duration)
        # Keep the anchor at the same relative position across the change.
        frac = (anchor_time - self._view_start) / span if span else 0.5
        self._set_view(anchor_time - frac * new_span,
                       anchor_time - frac * new_span + new_span)

    def wheelEvent(self, event):
        if self._duration <= 0:
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift-wheel pans instead of zooming.
            shift = self.view_span * (-0.20 if delta > 0 else 0.20)
            self._set_view(self._view_start + shift, self._view_end + shift)
        else:
            self._zoom_at(0.8 if delta > 0 else 1.25, self._time_at(event.position().x()))
        event.accept()

    # ----- geometry -----------------------------------------------------

    def _track_width(self) -> int:
        return max(self.width() - 2 * PAD, 1)

    def _x_of(self, seconds: float) -> float:
        return PAD + self._track_width() * (seconds - self._view_start) / self.view_span

    def _time_at(self, x: float) -> float:
        frac = (x - PAD) / self._track_width()
        return min(max(self._view_start + frac * self.view_span, 0.0), self._duration)

    def _thumb_time(self, i: int) -> float:
        """When thumbnail `i` was taken.

        The filmstrip decodes keyframes only, so frame i is keyframe i. If the
        two loaders disagree on count, fall back to even spacing.
        """
        if len(self._keyframes) == len(self._thumbs) and self._keyframes:
            return self._keyframes[i]
        if len(self._thumbs) > 1:
            return self._duration * i / (len(self._thumbs) - 1)
        return 0.0

    # ----- painting -----------------------------------------------------

    def resizeEvent(self, event):
        self._cache = None
        super().resizeEvent(event)

    def _build_cache(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        pix = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
        pix.setDevicePixelRatio(ratio)
        pix.fill(BG)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        w, track = self.width(), self._track_width()
        film = QRect(PAD, 0, track, FILM_H)
        wave = QRect(PAD, FILM_H, track, WAVE_H)
        ruler = QRect(PAD, FILM_H + WAVE_H, track, RULER_H)

        p.fillRect(film, FILM_BG)
        p.fillRect(wave, WAVE_BG)
        p.fillRect(ruler, RULER_BG)

        self._paint_film(p, film)
        self._paint_wave(p, wave, track)
        self._paint_ruler(p, ruler, w)
        p.end()
        return pix

    def _paint_film(self, p: QPainter, film: QRect) -> None:
        """Each frame is drawn from its own timestamp to the next one's.

        That keeps the strip correct when zoomed: frames spread apart rather
        than stretching, exactly like an NLE.
        """
        if not self._thumbs:
            return
        p.save()
        p.setClipRect(film)
        for i, image in enumerate(self._thumbs):
            t0 = self._thumb_time(i)
            t1 = self._thumb_time(i + 1) if i + 1 < len(self._thumbs) else self._duration
            x0, x1 = self._x_of(t0), self._x_of(t1)
            if x1 < film.left() or x0 > film.right():
                continue
            width = max(x1 - x0, 1.0)
            # Tile the frame across its slice rather than stretching one copy.
            drawn = 0.0
            while drawn < width:
                slice_w = min(THUMB_W, width - drawn)
                src_w = min(THUMB_W, slice_w * THUMB_H / FILM_H)
                p.drawImage(QRectF(x0 + drawn, 0, slice_w, FILM_H), image,
                            QRectF((THUMB_W - src_w) / 2, 0, src_w, THUMB_H))
                drawn += slice_w
        p.restore()

    def _paint_wave(self, p: QPainter, wave: QRect, track: int) -> None:
        if not self._peaks or self._duration <= 0:
            return
        p.setPen(QPen(WAVE_FG, 1))
        mid = wave.center().y() + 0.5
        half = wave.height() / 2 - 1
        n = len(self._peaks)
        for px in range(track):
            t = self._time_at(PAD + px)
            idx = min(int(t / self._duration * n), n - 1)
            h = max(self._peaks[idx] * half, 0.5)
            p.drawLine(QPointF(PAD + px, mid - h), QPointF(PAD + px, mid + h))

    def _paint_ruler(self, p: QPainter, ruler: QRect, w: int) -> None:
        if self._duration <= 0:
            return
        # Keyframe ticks. Zoomed in far enough that they are individually
        # meaningful, they brighten -- those are the free, lossless cut points.
        if self._keyframes:
            spacing = (self._x_of(self._keyframes[1]) - self._x_of(self._keyframes[0])
                       if len(self._keyframes) > 1 else 0)
            hot = spacing > 14
            p.setPen(QPen(KEYFRAME_HOT if hot else KEYFRAME, 1))
            last = -99.0
            for t in self._keyframes:
                x = self._x_of(t)
                if x < PAD - 1 or x > w - PAD + 1 or x - last < 3:
                    continue
                last = x
                p.drawLine(QPointF(x, ruler.top()), QPointF(x, ruler.top() + (6 if hot else 4)))

        p.setPen(QPen(RULER_FG, 1))
        font = p.font()
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        p.setFont(font)
        span = self.view_span
        step = next((s for s in _STEPS if span / s <= 9), _STEPS[-1])
        metrics = p.fontMetrics()
        t = (int(self._view_start / step)) * step
        while t <= self._view_end:
            if t >= self._view_start:
                x = self._x_of(t)
                label = _short_tc(t, step < 1)
                tw = metrics.horizontalAdvance(label)
                tx = min(max(x + 3, PAD), w - PAD - tw)
                p.drawLine(QPointF(x, ruler.top() + 7), QPointF(x, ruler.bottom()))
                p.drawText(QPointF(tx, ruler.bottom() - 5), label)
            t += step

    def paintEvent(self, event):
        if self._cache is None:
            self._cache = self._build_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)

        if self._duration <= 0:
            p.end()
            return

        h = self.height()
        x_in = max(min(self._x_of(self._in), self.width() + 50), -50)
        x_out = max(min(self._x_of(self._out), self.width() + 50), -50)

        # Everything outside the selection recedes.
        if x_in > 0:
            p.fillRect(QRectF(0, 0, x_in, h), DIM)
        if x_out < self.width():
            p.fillRect(QRectF(x_out, 0, self.width() - x_out, h), DIM)

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(self._accent, 1))
        p.drawLine(QPointF(x_in, 0), QPointF(x_in, h))
        p.drawLine(QPointF(x_out, 0), QPointF(x_out, h))
        p.fillRect(QRectF(x_in, 0, x_out - x_in, 2), self._accent)
        p.fillRect(QRectF(x_in, h - 2, x_out - x_in, 2), self._accent)

        self._draw_handle(p, x_in, left=True)
        self._draw_handle(p, x_out, left=False)

        # Playhead last so it always reads on top.
        x_pos = self._x_of(self._position)
        if -1 <= x_pos <= self.width() + 1:
            p.setPen(QPen(PLAYHEAD, 1))
            p.drawLine(QPointF(x_pos, 0), QPointF(x_pos, h))
            head = QPainterPath()
            head.moveTo(x_pos - 5, 0)
            head.lineTo(x_pos + 5, 0)
            head.lineTo(x_pos, 7)
            head.closeSubpath()
            p.fillPath(head, PLAYHEAD)

        if self._hover_x is not None and self._drag is None:
            self._draw_hover(p)
        p.end()

    def _draw_handle(self, p: QPainter, x: float, left: bool) -> None:
        h = self.height()
        rect = QRectF(x - HANDLE_W if left else x, (h - 30) / 2, HANDLE_W, 30)
        path = QPainterPath()
        path.addRoundedRect(rect, 2.5, 2.5)
        p.fillPath(path, self._accent)
        p.setPen(QPen(self._grip, 1))
        for i in (-2, 1):
            gx = rect.center().x() + i
            p.drawLine(QPointF(gx, rect.top() + 8), QPointF(gx, rect.bottom() - 8))

    def _draw_hover(self, p: QPainter) -> None:
        """Floating frame + timecode following the pointer."""
        t = self._time_at(self._hover_x)
        image = self._nearest_thumb(t)
        pad = 4
        tw, th = (THUMB_W, THUMB_H) if image is not None else (86, 0)
        label = fmt_tc(t)
        metrics = p.fontMetrics()
        text_h = metrics.height()
        box_w = max(tw, metrics.horizontalAdvance(label)) + pad * 2
        box_h = th + text_h + pad * 2 + (2 if image is not None else 0)

        x = min(max(self._hover_x - box_w / 2, 2), self.width() - box_w - 2)
        y = max(FILM_H - box_h - 6, 2)

        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, box_w, box_h), 4, 4)
        p.fillPath(path, POPUP_BG)
        p.setPen(QPen(POPUP_EDGE, 1))
        p.drawPath(path)
        if image is not None:
            p.drawImage(QRectF(x + (box_w - tw) / 2, y + pad, tw, th), image)
        p.setPen(QPen(QColor(230, 232, 236), 1))
        p.drawText(QRectF(x, y + box_h - text_h - pad, box_w, text_h),
                   int(Qt.AlignmentFlag.AlignCenter), label)

    def _nearest_thumb(self, t: float) -> QImage | None:
        if not self._thumbs:
            return None
        best = min(range(len(self._thumbs)), key=lambda i: abs(self._thumb_time(i) - t))
        return self._thumbs[best]

    # ----- interaction --------------------------------------------------

    def _hit(self, x: float) -> str | None:
        x_in, x_out = self._x_of(self._in), self._x_of(self._out)
        if abs(x - x_in) <= GRAB_PX:
            return "in"
        if abs(x - x_out) <= GRAB_PX:
            return "out"
        return None

    def _snap_in(self, t: float, disable: bool) -> tuple[float, bool]:
        """Pull the in-point onto a nearby keyframe unless overridden.

        Landing on one turns the export into a stream copy, so it is worth a
        little magnetism -- but never silently, and never without an escape.
        """
        if disable or not self._keyframes:
            return t, False
        tolerance = SNAP_PX * self.view_span / self._track_width()
        nearest = min(self._keyframes, key=lambda k: abs(k - t))
        if abs(nearest - t) <= tolerance:
            return nearest, True
        return t, False

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._duration <= 0:
            return
        x = event.position().x()
        self._drag = self._hit(x) or "seek"
        if self._drag == "seek":
            self.scrubbing.emit(True)
        self._apply_drag(x, event.modifiers())

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._drag is None:
            self._hover_x = x if self._duration > 0 else None
            over = self._hit(x) if self._duration > 0 else None
            self.setCursor(Qt.CursorShape.SizeHorCursor if over
                           else Qt.CursorShape.ArrowCursor)
            self.update()
            return
        self._apply_drag(x, event.modifiers())

    def leaveEvent(self, event):
        self._hover_x = None
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag == "seek":
            self.scrubbing.emit(False)
        self._drag = None
        self.update()

    def mouseDoubleClickEvent(self, event):
        if self._duration > 0:
            self.zoom_all() if self.zoomed else self.zoom_to_selection()

    def _apply_drag(self, x: float, modifiers) -> None:
        t = self._time_at(x)
        if self._drag == "in":
            # Alt is the usual "ignore snapping" modifier in editors.
            free = bool(modifiers & Qt.KeyboardModifier.AltModifier)
            snapped_t, snapped = self._snap_in(t, free)
            self.set_in(min(snapped_t, self._out), snapped)
            self.inChanged.emit(self._in)
        elif self._drag == "out":
            self.set_out(max(t, self._in))
            self.outChanged.emit(self._out)
        elif self._drag == "seek":
            self.set_position(t)
            self.seeked.emit(t)
