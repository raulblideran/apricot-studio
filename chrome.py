# Apricot Studio -- a small video trimmer.
# Copyright (C) 2026 Raul Blideran
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file for the full text.
"""The parts of a theme Qt stylesheets cannot express.

Qt's stylesheets can recolour anything and reshape almost nothing: there is no
clip-path, no transform, no text-transform, and border-radius only ever rounds.
A theme that wants notched corners, scanlines or a glitch has to paint them.

Everything here is inert unless the active theme asks for it. `ChamferButton`
falls straight through to Qt's own rendering when `chamfer` is 0, which is what
keeps the Default theme byte-identical to the version before themes existed --
it is not a reimplementation of the old look, it is the old code path.

The active theme is module state rather than a constructor argument because
every widget has to change together, and threading a theme through every call
site is how one of them gets missed.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QPushButton, QWidget

import theme

# Cyan and magenta, split either side of the text for a frame or two. Fixed
# rather than themed: chromatic aberration is an artefact of a bad signal, and
# a signal does not know what colour scheme it is being displayed in.
GLITCH_A = QColor(0, 240, 255)
GLITCH_B = QColor(255, 0, 90)
GLITCH_MS = 190              # how long a hover flickers before it settles
GLITCH_TICK = 24             # ms between redraws while it does

SCANLINE_GAP = 3             # px between lines
SCANLINE_ALPHA = 26          # out of 255, on every third row only

# The lines are drawn *light*, not dark. A darkening scanline is the obvious
# implementation and it is the wrong one here: over a #0a0d0e window it moves
# the pixel by a single value out of 255, so the effect is invisible on the
# surface that covers most of the interface, and shows up only over the
# filmstrip. A cool white at the same alpha reads as texture everywhere.
SCANLINE_COLOUR = QColor(214, 251, 255)

# There is no vignette, and that is a decision rather than an omission. Darkening
# the foot of the window is the other half of the CRT look, but the foot of this
# window is where the export button, the delete checkbox and the size estimate
# live. Measured: even the gentlest useful vignette (alpha 8) drops the delete
# red from 4.67:1 to 4.44:1, under the floor everything else here is held to,
# and it does it precisely on the control that destroys a file. The scanlines
# carry the effect on their own.

_theme: theme.Theme = theme.DEFAULT
_accent: str = theme.DEFAULT.default_accent
_quiet: bool = False


def set_look(thm: theme.Theme, accent: str | None = None) -> None:
    """Point every custom-painted widget at a new theme."""
    global _theme, _accent
    _theme = thm
    _accent = thm.normalise(accent)


def look() -> tuple[theme.Theme, str]:
    return _theme, _accent


def set_quiet(quiet: bool) -> None:
    """Hold the animated decoration still.

    Set while an export is running or the preview is playing. A glitch is a
    joke the first time and an obstruction while someone is reading a timecode
    off a moving picture, and the repaints are not free either.
    """
    global _quiet
    _quiet = bool(quiet)


def chamfer_polygon(width: float, height: float, cut: float) -> QPolygonF:
    """The notched rectangle, corners cut top-left and bottom-right.

    Kept as a function so the shape can be tested without a window. The cut is
    clamped to half the shorter side, because a notch larger than the button
    stops being a notch and starts being a triangle.
    """
    c = max(0.0, min(cut, min(width, height) / 2))
    return QPolygonF([
        QPointF(c, 0), QPointF(width, 0), QPointF(width, height - c),
        QPointF(width - c, height), QPointF(0, height), QPointF(0, c),
    ])


class ChamferButton(QPushButton):
    """A button that paints itself as a notched polygon under a chamfered theme.

    Under any other theme this is a plain QPushButton and the stylesheet does
    all the work, so nothing about the Default look passes through new code.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._glitch_left = 0
        self._timer: QTimer | None = None

    # ----- glitch ---------------------------------------------------------

    def _glitching(self) -> bool:
        return bool(self._glitch_left) and _theme.glitch and not _quiet

    def enterEvent(self, event):
        if _theme.glitch and not _quiet and self.isEnabled():
            self._glitch_left = GLITCH_MS
            if self._timer is None:
                self._timer = QTimer(self)
                self._timer.timeout.connect(self._tick)
            self._timer.start(GLITCH_TICK)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._stop_glitch()
        super().leaveEvent(event)

    def _tick(self) -> None:
        self._glitch_left -= GLITCH_TICK
        if self._glitch_left <= 0:
            self._stop_glitch()
        self.update()

    def _stop_glitch(self) -> None:
        self._glitch_left = 0
        if self._timer is not None:
            self._timer.stop()
        self.update()

    # ----- painting -------------------------------------------------------

    def paintEvent(self, event):
        # The swatch is a small filled square the stylesheet already draws, and
        # a themed run that shows it is one that has no chamfer anyway.
        if not _theme.chamfer or self.objectName() == "swatch":
            super().paintEvent(event)
            return

        t, a = _theme, _accent
        primary = self.objectName() == "primary"
        enabled = self.isEnabled()

        if not enabled:
            fill = t.disabled_bg
            edge = t.disabled_bg
            ink = t.disabled_text
        elif primary:
            fill = theme.lighten(a, 0.14) if self.underMouse() else a
            edge = theme.lighten(a, 0.3)
            ink = theme.on_accent(a)
        else:
            fill = t.pressed if self.isDown() else (
                theme.lighten(t.surface, 0.08) if self.underMouse() else t.surface)
            edge = a if self.underMouse() else t.border
            ink = t.text

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = chamfer_polygon(self.width() - 1, self.height() - 1, t.chamfer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(fill))
        p.drawPolygon(shape)
        p.setPen(QPen(QColor(edge), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(shape)

        # A tick in the cut corner, the way the game marks a live control.
        if enabled and not primary:
            p.setPen(QPen(QColor(a), 1))
            c = t.chamfer
            p.drawLine(0, self.height() - 1, c, self.height() - 1 - c)

        label = self.text().upper()
        font = QFont(self.font())
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, t.letter_spacing)
        if primary:
            font.setBold(True)
        p.setFont(font)
        box = QRectF(0, 0, self.width(), self.height())
        flags = int(Qt.AlignmentFlag.AlignCenter)

        if self._glitching():
            # Split the channels a pixel apart. The offset walks with the
            # countdown so it settles rather than stopping mid-flicker.
            step = 1 + (self._glitch_left // GLITCH_TICK) % 2
            for colour, dx in ((GLITCH_A, -step), (GLITCH_B, step)):
                ghost = QColor(colour)
                ghost.setAlpha(150)
                p.setPen(QPen(ghost, 1))
                p.drawText(box.translated(dx, 0), flags, label)

        p.setPen(QPen(QColor(ink), 1))
        p.drawText(box, flags, label)
        p.end()


class ScanlineOverlay(QWidget):
    """Horizontal lines over the whole window, and a darkening towards the foot.

    A child of the widget it covers rather than a paint step inside it, so it
    sits above every control without any of them knowing. It is transparent to
    the mouse, so it cannot swallow a click on the timeline underneath.

    The alpha is deliberately low. tests/test_units.py checks that text still
    clears the 4.5:1 contrast floor with the overlay on top, because a
    decoration that makes a timecode harder to read has cost more than it gave.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
            self.raise_()
        return False

    def paintEvent(self, event):
        if not _theme.scanlines:
            return
        p = QPainter(self)
        h, w = self.height(), self.width()

        line = QColor(SCANLINE_COLOUR)
        line.setAlpha(SCANLINE_ALPHA)
        p.setPen(QPen(line, 1))
        for y in range(0, h, SCANLINE_GAP):
            p.drawLine(0, y, w, y)

        p.end()


def overlay_contrast(text: str, background: str) -> float:
    """Contrast between two colours once the scanline overlay is over both.

    The lines cover one row in `SCANLINE_GAP`, so on average they lift
    everything by that fraction of their alpha towards `SCANLINE_COLOUR`.
    Lifting a near-black background is what costs contrast here -- both the
    text and what it sits on are behind the overlay, which is why this is not
    simply "the background got lighter".
    """
    lift = (SCANLINE_ALPHA / 255) / SCANLINE_GAP
    under = lambda c: theme.mix(c, SCANLINE_COLOUR.name(), lift)
    return theme.contrast(under(text), under(background))
