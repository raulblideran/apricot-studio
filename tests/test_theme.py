"""The accent palette and the styling derived from it.

The accent is used for text, not only for fills, so "does it look nice" is not
the bar -- each colour has to be readable on the window background and telling
them apart has to be possible. Both are measured rather than eyeballed.
"""

from __future__ import annotations

import itertools
import math
import re
import unittest

import theme

# WCAG AA for normal text. The badge and the selection duration are rendered in
# the accent, so this is the applicable threshold rather than the 3:1 for large
# text.
MIN_CONTRAST = 4.5

# CIE76 colour difference. Around 2 is "just noticeable"; 15 is comfortably
# distinct at a glance, which is what a palette needs.
MIN_DISTANCE = 15.0


def _lab(colour: str) -> tuple[float, float, float]:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in theme._rgb(colour))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def distance(first: str, second: str) -> float:
    return math.dist(_lab(first), _lab(second))


class Palette(unittest.TestCase):
    def test_offers_a_default_plus_ten(self):
        self.assertEqual(len(theme.ACCENTS), 11)

    def test_the_default_is_apricot(self):
        self.assertEqual(theme.DEFAULT_ACCENT, "#e27125")
        self.assertIn(("Apricot", "#e27125"), theme.ACCENTS)

    def test_the_default_is_among_the_choices(self):
        self.assertIn(theme.DEFAULT_ACCENT, [c for _, c in theme.ACCENTS])

    def test_every_entry_is_a_usable_colour(self):
        for name, colour in theme.ACCENTS:
            self.assertRegex(colour, r"^#[0-9a-f]{6}$", f"{name} is not lowercase hex")
            self.assertTrue(theme.is_valid(colour))

    def test_names_and_colours_are_unique(self):
        names = [n for n, _ in theme.ACCENTS]
        colours = [c for _, c in theme.ACCENTS]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(set(colours)), len(colours))


class Readability(unittest.TestCase):
    def test_every_accent_is_readable_on_the_window(self):
        for name, colour in theme.ACCENTS:
            ratio = theme.contrast(colour, theme.BACKGROUND)
            self.assertGreaterEqual(
                ratio, MIN_CONTRAST,
                f"{name} ({colour}) is only {ratio:.2f}:1 on the background")

    def test_every_accent_is_readable_on_the_sunken_surface(self):
        # Text fields use a darker fill; the focus ring and selection sit there.
        for name, colour in theme.ACCENTS:
            self.assertGreaterEqual(theme.contrast(colour, theme.SUNKEN),
                                    MIN_CONTRAST, f"{name} on the sunken surface")

    def test_text_on_the_accent_is_readable(self):
        # The export button puts text on top of the accent.
        for name, colour in theme.ACCENTS:
            ratio = theme.contrast(colour, theme.on_accent(colour))
            self.assertGreaterEqual(ratio, MIN_CONTRAST,
                                    f"button text on {name} is {ratio:.2f}:1")

    def test_on_accent_chooses_dark_text_for_a_light_colour(self):
        self.assertNotEqual(theme.on_accent("#f5d76e"), "#ffffff")

    def test_on_accent_chooses_light_text_for_a_dark_colour(self):
        # Not in the shipped palette, but the helper must not assume.
        self.assertEqual(theme.on_accent("#1b2a6b"), "#ffffff")

    def test_accents_are_telling_apart_from_each_other(self):
        for (a, first), (b, second) in itertools.combinations(theme.ACCENTS, 2):
            gap = distance(first, second)
            self.assertGreaterEqual(gap, MIN_DISTANCE,
                                    f"{a} and {b} are only dE {gap:.1f} apart")

    def test_accents_do_not_impersonate_the_semantic_colours(self):
        # Green means "this export is a lossless copy" and red means "this will
        # delete your original". An accent close enough to be mistaken for
        # either would make the interface lie.
        for name, colour in theme.ACCENTS:
            self.assertGreaterEqual(
                distance(colour, theme.LOSSLESS), MIN_DISTANCE,
                f"{name} could be mistaken for the lossless badge")
            self.assertGreaterEqual(
                distance(colour, theme.DESTRUCTIVE_TEXT), MIN_DISTANCE,
                f"{name} could be mistaken for the delete warning")


class Normalising(unittest.TestCase):
    def test_accepts_the_shipped_palette(self):
        for _, colour in theme.ACCENTS:
            self.assertEqual(theme.normalise(colour), colour)

    def test_accepts_a_colour_of_the_user_s_own(self):
        self.assertEqual(theme.normalise("#123456"), "#123456")

    def test_uppercase_is_accepted_and_lowered(self):
        self.assertEqual(theme.normalise("#AABBCC"), "#aabbcc")

    def test_falls_back_rather_than_crashing(self):
        # A settings file is user-editable and survives upgrades, so anything
        # can turn up here.
        for junk in ("", "  ", "nonsense", "#12345", "#1234567", "red",
                     "#GGGGGG", None, 42, [], "#e27125 "):
            self.assertEqual(theme.normalise(junk), theme.DEFAULT_ACCENT,
                             f"{junk!r} should have fallen back")

    def test_is_valid_agrees_with_normalise(self):
        for junk in ("", "nope", "#12345", None, 7):
            self.assertFalse(theme.is_valid(junk))


class Derivation(unittest.TestCase):
    def test_lighten_moves_towards_white(self):
        self.assertGreater(theme.luminance(theme.lighten("#808080", 0.5)),
                           theme.luminance("#808080"))

    def test_muted_moves_towards_the_background(self):
        for _, colour in theme.ACCENTS:
            self.assertLess(theme.contrast(theme.muted(colour), theme.BACKGROUND),
                            theme.contrast(colour, theme.BACKGROUND),
                            "a disabled control should recede, not shout")

    def test_mix_endpoints(self):
        self.assertEqual(theme.mix("#000000", "#ffffff", 0.0), "#000000")
        self.assertEqual(theme.mix("#000000", "#ffffff", 1.0), "#ffffff")
        self.assertEqual(theme.mix("#000000", "#ffffff", 0.5), "#808080")

    def test_contrast_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(theme.contrast("#000000", "#ffffff"), 21.0, places=1)
        self.assertAlmostEqual(theme.contrast("#123456", "#123456"), 1.0, places=6)
        self.assertAlmostEqual(theme.contrast("#abcdef", "#123456"),
                               theme.contrast("#123456", "#abcdef"), places=6)


class Stylesheet(unittest.TestCase):
    def test_the_chosen_accent_reaches_the_sheet(self):
        for _, colour in theme.ACCENTS:
            self.assertIn(colour, theme.stylesheet(colour))

    def test_the_previous_accent_does_not_linger(self):
        sheet = theme.stylesheet("#3d92e0")
        self.assertNotIn("#e27125", sheet)

    def test_an_invalid_accent_still_produces_a_sheet(self):
        self.assertIn(theme.DEFAULT_ACCENT, theme.stylesheet("nonsense"))

    def test_it_styles_the_controls_that_matter(self):
        sheet = theme.stylesheet()
        for selector in ("QPushButton#primary", "QLineEdit:focus", "QLabel#badge",
                         "QProgressBar::chunk", "QPushButton#swatch",
                         "QComboBox QAbstractItemView"):
            self.assertIn(selector, sheet, f"{selector} lost its styling")

    def test_semantic_colours_ignore_the_accent(self):
        # Whatever the accent, "lossless" stays green and "delete" stays red.
        for _, colour in theme.ACCENTS:
            sheet = theme.stylesheet(colour)
            self.assertIn(theme.LOSSLESS, sheet)
            self.assertIn(theme.DESTRUCTIVE, sheet)

    def test_no_placeholder_survives_formatting(self):
        # A missed brace would silently ship "{a}" into the sheet, and Qt would
        # drop the whole rule rather than complain.
        sheet = theme.stylesheet()
        self.assertNotRegex(sheet, r"\{\s*[a-z_]+\s*\}",
                            "an f-string field was left unfilled")

    def test_every_declared_colour_is_a_real_colour(self):
        # Qt ignores rules it cannot parse, so a typo'd hex disappears quietly.
        for value in re.findall(r":\s*(#[0-9a-fA-F]+)", theme.stylesheet()):
            self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", f"{value} is malformed")


if __name__ == "__main__":
    unittest.main()
