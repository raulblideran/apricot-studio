"""Accent colours, and the stylesheet derived from whichever one is chosen.

The accent is the interactive colour: the export button, the in/out handles,
selection, focus rings. It is a preference and carries no meaning.

Two colours deliberately do *not* follow it, because they are statements rather
than decoration -- green for "this export is a lossless copy" and red for
"this will delete your original". If those tracked the accent, picking a green
accent would make the delete checkbox look reassuring.

Every accent here clears 4.5:1 against the window background, because the accent
is used for text (the badge, the selection duration) and not only for fills.
"""

from __future__ import annotations

# Surfaces. The accent is layered over these, so contrast is measured against
# BACKGROUND rather than against black.
BACKGROUND = "#1c1e22"
SURFACE = "#2a2e35"
SUNKEN = "#14161a"
BORDER = "#383d47"
TEXT = "#e6e8ec"
TEXT_DIM = "#9aa2b1"

# Meaning, not preference -- see the module docstring.
LOSSLESS = "#6ee7a0"
DESTRUCTIVE = "#e05252"
DESTRUCTIVE_TEXT = "#ff8080"

DEFAULT_ACCENT = "#e27125"

# Eleven choices spanning the hue wheel at roughly even lightness, so switching
# accent changes the colour without changing how heavy the interface looks.
# Verified: worst contrast on BACKGROUND is 4.51:1, closest perceptual pair is
# dE 17.1, both comfortable.
ACCENTS: list[tuple[str, str]] = [
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
]

ACCENT_NAMES = {value: name for name, value in ACCENTS}


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


def normalise(colour: str | None) -> str:
    """A usable accent, falling back to the default for anything unexpected."""
    return colour.lower() if colour and is_valid(colour) else DEFAULT_ACCENT


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


def muted(colour: str) -> str:
    """The accent for a disabled control: present, clearly not clickable."""
    return mix(colour, BACKGROUND, 0.68)


def stylesheet(accent: str | None = None) -> str:
    """The whole application stylesheet, built around one accent colour."""
    a = normalise(accent)
    return f"""
QMainWindow, QWidget {{ background: {BACKGROUND}; color: {TEXT}; }}
QLabel {{ color: {TEXT}; }}
QLabel#meta {{ color: {TEXT_DIM}; }}
QLabel#status {{ color: {TEXT_DIM}; }}
QLabel#title {{ font-size: 13px; font-weight: 600; }}
QLabel#duration {{ color: {a}; font-weight: 600; }}
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 5px 11px; color: {TEXT};
}}
QPushButton:hover {{ background: {lighten(SURFACE, 0.06)}; }}
QPushButton:pressed {{ background: #23262c; }}
QPushButton:disabled {{ color: #6a7181; background: #24272d; }}
QPushButton#primary {{
    background: {a}; border: 1px solid {a}; color: {on_accent(a)};
    font-weight: 600; padding: 7px 22px;
}}
QPushButton#primary:hover {{ background: {lighten(a, 0.14)}; border-color: {lighten(a, 0.14)}; }}
QPushButton#primary:disabled {{
    background: {muted(a)}; border-color: {muted(a)}; color: {mix(on_accent(a), BACKGROUND, 0.45)};
}}
QPushButton#step {{ padding: 4px 7px; font-family: monospace; }}
QPushButton#swatch {{
    background: {a}; border: 1px solid {lighten(a, 0.2)}; border-radius: 5px;
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
}}
QPushButton#swatch:hover {{ border-color: {TEXT}; }}
QLineEdit {{
    background: {SUNKEN}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 5px 8px; color: {TEXT}; selection-background-color: {a};
    selection-color: {on_accent(a)};
}}
QLineEdit:focus {{ border-color: {a}; }}
QLineEdit#tc {{ font-family: monospace; max-width: 130px; }}
QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 4px 8px; color: {TEXT}; min-width: 96px;
}}
QComboBox:hover {{ background: {lighten(SURFACE, 0.06)}; }}
QComboBox:disabled {{ color: #6a7181; background: #24272d; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background: #22252b; border: 1px solid {BORDER}; color: {TEXT};
    selection-background-color: {a}; selection-color: {on_accent(a)}; outline: none;
}}
QMenu {{ background: #22252b; border: 1px solid {BORDER}; color: {TEXT}; }}
QMenu::item:selected {{ background: {a}; color: {on_accent(a)}; }}
QLabel#badge {{ color: {a}; font-weight: 600; }}
QLabel#badgeFast {{ color: {LOSSLESS}; font-weight: 600; }}
QCheckBox {{ color: {TEXT_DIM}; spacing: 6px; }}
QCheckBox:disabled {{ color: #5a616f; }}
/* Armed, this destroys a file. It should not look like the other options, and
   it deliberately ignores the accent. */
QCheckBox:checked {{ color: {DESTRUCTIVE_TEXT}; font-weight: 600; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border-radius: 3px;
                       border: 1px solid #4a505d; background: {SUNKEN}; }}
QCheckBox::indicator:checked {{ background: {DESTRUCTIVE}; border-color: {DESTRUCTIVE}; }}
QProgressBar {{
    background: {SUNKEN}; border: 1px solid {BORDER}; border-radius: 5px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {a}; border-radius: 4px; }}
QFrame#sep {{ background: {SURFACE}; max-height: 1px; border: none; }}
"""
