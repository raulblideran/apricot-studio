# Apricot Studio -- a small video trimmer.
# Copyright (C) 2026 Raul Blideran
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file for the full text.
"""Themes, accent colours, and the stylesheet derived from the pair.

A theme is the whole shell: surfaces, borders, typography, the timeline's
palette, and whether the chrome is drawn with square corners or notched ones.
An accent is the interactive colour inside that shell -- the export button, the
in/out handles, selection, focus rings.

The two axes are not equal, and deliberately so. Default offers eleven accents
because its shell is neutral enough to carry any of them. Cyberpunk offers
none: the yellow *is* the theme, and a user-chosen teal inside it would only be
a worse Default. A theme whose `accents` list is empty hides the picker.

Two colours do not follow the accent in either theme, because they are
statements rather than decoration -- green for "this export is a lossless copy"
and red for "this will delete your original". If those tracked the accent,
picking a green accent would make the delete checkbox look reassuring. They are
per-theme rather than global, because neon on near-black and muted on charcoal
are different colours saying the same two things.

Every accent clears 4.5:1 against its own theme's window background, because
the accent is used for text (the badge, the selection duration) and not only
for fills. tests/test_theme.py measures that for every theme rather than
trusting the eye.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


# --------------------------------------------------------------------------
# Colour maths. None of this knows about themes.
# --------------------------------------------------------------------------

def _rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def is_valid(colour: str) -> bool:
    """Whether a stored preference is still something we can paint with."""
    if not isinstance(colour, str) or not colour.startswith("#") or len(colour) != 7:
        return False
    try:
        _rgb(colour)
    except ValueError:
        return False
    return True


def luminance(colour: str) -> float:
    """WCAG relative luminance."""
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(first: str, second: str) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    a, b = luminance(first), luminance(second)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def mix(first: str, second: str, amount: float) -> str:
    """`first` blended `amount` of the way towards `second`."""
    a, b = _rgb(first), _rgb(second)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(a, b)))


def lighten(colour: str, amount: float = 0.15) -> str:
    return mix(colour, "#ffffff", amount)


def on_accent(colour: str) -> str:
    """Readable text to sit on top of the accent.

    Computed rather than hardcoded so a dark accent stays legible -- the
    original palette happened to be light enough for dark text throughout, and
    hardcoding that would break the first time someone picked a deep colour.
    """
    dark = mix(colour, "#000000", 0.85)
    return dark if contrast(colour, dark) >= contrast(colour, "#ffffff") else "#ffffff"


# --------------------------------------------------------------------------
# The timeline paints itself, so it takes colours rather than a stylesheet.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TimelineColours:
    """What timeline.py paints with, minus the accent, which it is handed.

    These are held as values rather than derived from the surfaces because the
    Default set was tuned by hand and several sit a single step off the nearest
    surface colour. Deriving them would change today's look by a shade for no
    gain, and the whole point of the Default theme is that it does not move.
    """
    bg: str
    film_bg: str
    wave_bg: str
    wave_fg: str
    ruler_bg: str
    ruler_fg: str
    keyframe: str
    keyframe_hot: str
    dim: str
    dim_alpha: int
    playhead: str
    popup_bg: str
    popup_alpha: int
    popup_edge: str
    text: str
    # A border drawn round the whole widget in every state, empty video
    # included. "" means no border, which is what keeps Default as it was.
    outline: str = ""


# --------------------------------------------------------------------------
# Themes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Theme:
    """One complete look. `key` is what lands in QSettings."""
    key: str
    name: str

    # Surfaces. Accents are layered over these, so contrast is measured
    # against `background` rather than against black.
    background: str
    surface: str
    sunken: str
    border: str
    text: str
    text_dim: str

    # Meaning, not preference -- see the module docstring.
    lossless: str
    destructive: str
    destructive_text: str

    # State colours the stylesheet needs but nothing derives well.
    pressed: str
    disabled_text: str
    disabled_bg: str
    popup_bg: str
    indicator_border: str
    checkbox_disabled: str

    default_accent: str
    timeline: TimelineColours

    # Empty means this theme owns its colour and the picker is hidden.
    accents: tuple[tuple[str, str], ...] = ()

    # "" leaves Qt's default font alone.
    font_family: str = ""
    mono_family: str = "monospace"
    letter_spacing: float = 100.0   # percentage; 100 is the font's own spacing

    radius: int = 5                 # QSS border-radius
    chamfer: int = 0                # >0 cuts the corners off buttons, in px
    scanlines: bool = False
    glitch: bool = False

    @property
    def accent_names(self) -> dict[str, str]:
        return {value: name for name, value in self.accents}

    def normalise(self, colour: str | None) -> str:
        """A usable accent for this theme.

        A theme with no palette of its own always answers with its own colour,
        so a value left behind by another theme cannot leak through a switch.
        """
        if not self.accents:
            return self.default_accent
        return colour.lower() if colour and is_valid(colour) else self.default_accent


DEFAULT = Theme(
    key="default",
    name="Default",
    background="#1c1e22",
    surface="#2a2e35",
    sunken="#14161a",
    border="#383d47",
    text="#e6e8ec",
    text_dim="#9aa2b1",
    lossless="#6ee7a0",
    destructive="#e05252",
    destructive_text="#ff8080",
    pressed="#23262c",
    disabled_text="#6a7181",
    disabled_bg="#24272d",
    popup_bg="#22252b",
    indicator_border="#4a505d",
    checkbox_disabled="#5a616f",
    default_accent="#e27125",
    # Eleven choices spanning the hue wheel at roughly even lightness, so
    # switching accent changes the colour without changing how heavy the
    # interface looks. Verified: worst contrast on the background is 4.51:1,
    # closest perceptual pair is dE 17.1, both comfortable.
    accents=(
        ("Apricot", "#e27125"),
        ("Amber", "#e8a33d"),
        ("Coral", "#e2564d"),
        ("Rose", "#e06189"),
        ("Purple", "#a87ae8"),
        ("Indigo", "#6f7ce8"),
        ("Azure", "#3d92e0"),
        ("Teal", "#26a69a"),
        ("Green", "#5aab4f"),
        ("Lime", "#9bbf3c"),
        ("Slate", "#8b93a6"),
    ),
    timeline=TimelineColours(
        bg="#1c1e22",
        film_bg="#141619",
        wave_bg="#181a1e",
        wave_fg="#60aaff",
        ruler_bg="#16181c",
        ruler_fg="#969ca8",
        keyframe="#586070",
        keyframe_hot="#78dc96",
        dim="#0a0b0d",
        dim_alpha=168,
        playhead="#ffffff",
        popup_bg="#101215",
        popup_alpha=245,
        popup_edge="#464c58",
        text="#e6e8ec",
    ),
)


# NetWatch: crimson chrome and teal readout over near-black, sampled from the
# Netrunner terminal rather than invented -- #ff0056 for the accent, #a10036 for
# the filled bars and borders, #24d3d0 for the readout, #010101 for the ground.
#
# One deliberate departure from the reference. There, the body text is crimson
# too and teal is only for descriptions; here the accent has to stay tellable
# apart from ordinary text, because it is what marks the badge and the selection
# duration. So the two swap roles: crimson takes the chrome, the borders and the
# accent, and the teal readout -- which the reference already uses for every
# button label -- becomes the body text. It reads as the same terminal and the
# accent still means something.
#
# The other departure is forced. Red already had a job in this interface, and it
# was "this will delete your original"; a crimson accent leaves that signal
# nowhere to sit. The warning is therefore hazard orange, dE 49 from the accent
# and the only warm colour in the theme that is not crimson. Measured, not
# eyeballed -- tests/test_theme.py runs the same checks over every theme.
CYBERPUNK = Theme(
    key="cyberpunk",
    name="Cyberpunk",
    background="#010101",
    surface="#12040a",
    sunken="#000000",
    border="#a10036",              # the filled-bar crimson; the NetWatch tell
    text="#2ee0dd",
    text_dim="#1a9b9d",
    lossless="#00ff9f",
    destructive="#ff5c00",
    destructive_text="#ff8c3d",
    pressed="#1e0610",
    disabled_text="#5a2a3a",
    disabled_bg="#0a0206",
    popup_bg="#0a0206",
    indicator_border="#a10036",
    checkbox_disabled="#4a2430",
    default_accent="#ff0056",
    accents=(),                       # the crimson is the theme; see above
    font_family="Rajdhani",
    mono_family="monospace",
    letter_spacing=103.0,
    radius=0,
    chamfer=7,
    scanlines=True,
    glitch=True,
    timeline=TimelineColours(
        bg="#010101",
        film_bg="#000000",
        wave_bg="#070208",
        wave_fg="#24d3d0",
        ruler_bg="#050205",
        ruler_fg="#1a9b9d",
        keyframe="#a10036",
        keyframe_hot="#00ff9f",
        dim="#000000",
        dim_alpha=190,
        # Not crimson. The in/out handles are painted in the accent, and a
        # crimson playhead between two crimson handles is three of the same.
        playhead="#ffd6e0",
        popup_bg="#0a0206",
        popup_alpha=248,
        popup_edge="#a10036",
        text="#2ee0dd",
        # Empty, the timeline is otherwise a dark rectangle on a dark window
        # with nothing to say it is there. Matches `border` above, so the
        # timeline is edged like every other framed control in the theme.
        outline="#a10036",
    ),
)


THEMES: dict[str, Theme] = {t.key: t for t in (DEFAULT, CYBERPUNK)}


def get(key: str | None) -> Theme:
    """The named theme, falling back to Default for anything unrecognised.

    Same forgiving contract as `normalise` has for accents: a preference file
    written by a newer version, or edited by hand, must not stop the app
    opening a window.
    """
    if isinstance(key, str):
        return THEMES.get(key.strip().lower(), DEFAULT)
    return DEFAULT


# --------------------------------------------------------------------------
# Back-compatible module-level names, all pointing at Default.
# --------------------------------------------------------------------------

BACKGROUND = DEFAULT.background
SURFACE = DEFAULT.surface
SUNKEN = DEFAULT.sunken
BORDER = DEFAULT.border
TEXT = DEFAULT.text
TEXT_DIM = DEFAULT.text_dim
LOSSLESS = DEFAULT.lossless
DESTRUCTIVE = DEFAULT.destructive
DESTRUCTIVE_TEXT = DEFAULT.destructive_text
DEFAULT_ACCENT = DEFAULT.default_accent
ACCENTS: list[tuple[str, str]] = list(DEFAULT.accents)
ACCENT_NAMES = DEFAULT.accent_names


def normalise(colour: str | None, thm: Theme | None = None) -> str:
    """A usable accent, falling back to the theme's default."""
    return (thm or DEFAULT).normalise(colour)


def muted(colour: str, thm: Theme | None = None) -> str:
    """The accent for a disabled control: present, clearly not clickable."""
    return mix(colour, (thm or DEFAULT).background, 0.68)


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# Every weight the interface asks for. Qt synthesises a missing weight by
# smearing the outline, which on a condensed face looks like a different font
# rather than a bolder one, so the real files are shipped.
FONT_FILES = (
    "Rajdhani-Regular.ttf",
    "Rajdhani-Medium.ttf",
    "Rajdhani-SemiBold.ttf",
    "Rajdhani-Bold.ttf",
)

# Used when the bundled files are missing -- a source checkout without them, or
# a package that forgot to install them. Condensed technical faces first, so
# the fallback is at least the right shape.
FONT_FALLBACK = "DejaVu Sans Condensed, Noto Sans Display, sans-serif"


def font_stack(thm: Theme) -> str:
    """The font-family list for a theme, always ending somewhere real."""
    if not thm.font_family:
        return ""
    return f"{thm.font_family}, {FONT_FALLBACK}"


def load_fonts(directory: str | None = None) -> list[str]:
    """Register the bundled faces with Qt. Returns the families now available.

    Qt is imported here rather than at module scope so that the colour maths
    above stays usable -- and testable -- without a QGuiApplication, which
    QFontDatabase requires. A missing or unreadable file is a downgrade to the
    fallback stack, not a crash: the theme is worth less without its typeface
    but the app still runs.
    """
    from PyQt6.QtGui import QFontDatabase

    root = directory or FONT_DIR
    families: list[str] = []
    for name in FONT_FILES:
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        added = QFontDatabase.addApplicationFont(path)
        if added != -1:
            families.extend(QFontDatabase.applicationFontFamilies(added))
    return sorted(set(families))


# --------------------------------------------------------------------------
# The stylesheet
# --------------------------------------------------------------------------

def stylesheet(thm: Theme | str | None = None,
               accent: str | None = None) -> str:
    """The whole application stylesheet, built around one theme and accent.

    `thm` takes a Theme or a theme key. A *colour* passed there is read as the
    accent instead, because the accent used to be the first argument: `get`
    swallowing an unknown key is the right answer for a preference someone
    edited by hand, and the wrong one for a call that plainly meant the accent.
    Handing back a default-accented sheet would be a bug with no symptom.
    """
    if isinstance(thm, str) and thm.startswith("#"):
        thm, accent = None, thm
    t = thm if isinstance(thm, Theme) else get(thm)
    a = t.normalise(accent)
    r = t.radius
    stack = font_stack(t)

    # Under a chamfered theme the buttons are polygons drawn by chrome.py, so
    # the sheet has to get out of the way -- otherwise Qt paints its own
    # rounded rectangle underneath and the notch shows a corner of it.
    if t.chamfer:
        buttons = f"""
QPushButton {{
    background: transparent; border: none;
    padding: 5px 13px; color: {t.text};
}}
QPushButton:disabled {{ color: {t.disabled_text}; }}
QPushButton#primary {{ font-weight: 600; padding: 7px 24px; color: {on_accent(a)}; }}
QPushButton#step {{ padding: 4px 8px; font-family: {t.mono_family}; }}
"""
    else:
        buttons = f"""
QPushButton {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: {r}px;
    padding: 5px 11px; color: {t.text};
}}
QPushButton:hover {{ background: {lighten(t.surface, 0.06)}; }}
QPushButton:pressed {{ background: {t.pressed}; }}
QPushButton:disabled {{ color: {t.disabled_text}; background: {t.disabled_bg}; }}
QPushButton#primary {{
    background: {a}; border: 1px solid {a}; color: {on_accent(a)};
    font-weight: 600; padding: 7px 22px;
}}
QPushButton#primary:hover {{ background: {lighten(a, 0.14)}; border-color: {lighten(a, 0.14)}; }}
QPushButton#primary:disabled {{
    background: {muted(a, t)}; border-color: {muted(a, t)}; color: {mix(on_accent(a), t.background, 0.45)};
}}
QPushButton#step {{ padding: 4px 7px; font-family: {t.mono_family}; }}
"""

    sheet = f"""
QMainWindow, QWidget {{ background: {t.background}; color: {t.text}; }}
QLabel {{ color: {t.text}; }}
QLabel#meta {{ color: {t.text_dim}; }}
QLabel#status {{ color: {t.text_dim}; }}
QLabel#title {{ font-size: 13px; font-weight: 600; }}
QLabel#duration {{ color: {a}; font-weight: 600; }}
{buttons.strip()}
QPushButton#swatch {{
    background: {a}; border: 1px solid {lighten(a, 0.2)}; border-radius: {r}px;
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
}}
QPushButton#swatch:hover {{ border-color: {t.text}; }}
QLineEdit {{
    background: {t.sunken}; border: 1px solid {t.border}; border-radius: {r}px;
    padding: 5px 8px; color: {t.text}; selection-background-color: {a};
    selection-color: {on_accent(a)};
}}
QLineEdit:focus {{ border-color: {a}; }}
QLineEdit#tc {{ font-family: {t.mono_family}; max-width: 130px; }}
QLabel#clock {{ font-family: {t.mono_family}; }}
QComboBox {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: {r}px;
    padding: 4px 8px; color: {t.text}; min-width: 96px;
}}
QComboBox:hover {{ background: {lighten(t.surface, 0.06)}; }}
QComboBox:disabled {{ color: {t.disabled_text}; background: {t.disabled_bg}; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background: {t.popup_bg}; border: 1px solid {t.border}; color: {t.text};
    selection-background-color: {a}; selection-color: {on_accent(a)}; outline: none;
}}
QMenu {{ background: {t.popup_bg}; border: 1px solid {t.border}; color: {t.text}; }}
QMenu::item:selected {{ background: {a}; color: {on_accent(a)}; }}
QLabel#badge {{ color: {a}; font-weight: 600; }}
QLabel#badgeFast {{ color: {t.lossless}; font-weight: 600; }}
QCheckBox {{ color: {t.text_dim}; spacing: 6px; }}
QCheckBox:disabled {{ color: {t.checkbox_disabled}; }}
/* Armed, this destroys a file. It should not look like the other options, and
   it deliberately ignores the accent. */
QCheckBox:checked {{ color: {t.destructive_text}; font-weight: 600; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border-radius: {min(r, 3)}px;
                       border: 1px solid {t.indicator_border}; background: {t.sunken}; }}
QCheckBox::indicator:checked {{ background: {t.destructive}; border-color: {t.destructive}; }}
QProgressBar {{
    background: {t.sunken}; border: 1px solid {t.border}; border-radius: {r}px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {a}; border-radius: {max(r - 1, 0)}px; }}
QFrame#sep {{ background: {t.surface}; max-height: 1px; border: none; }}
"""

    if stack:
        # Ahead of everything, so a more specific rule can still override it.
        sheet = f"* {{ font-family: {stack}; }}\n{sheet.lstrip()}"

    if t.chamfer:
        # Tooltips are the one surface Qt styles itself, and a system-themed
        # light tooltip over near-black is a hole in the interface.
        sheet += f"""
QToolTip {{
    background: {t.popup_bg}; color: {t.text};
    border: 1px solid {a}; padding: 3px 6px;
}}
"""
    return sheet
