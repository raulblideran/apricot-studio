"""The themes, the accent palette, and the styling derived from the pair.

The accent is used for text, not only for fills, so "does it look nice" is not
the bar -- each colour has to be readable on the window background and telling
them apart has to be possible. Both are measured rather than eyeballed.

Every guarantee here is checked against *each theme's own* surfaces. A palette
that clears the contrast floor on charcoal says nothing about how it reads on
near-black, and a theme that shipped without being measured would be a theme
that looks fine to whoever chose it and to nobody else.
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


def every_theme() -> list[theme.Theme]:
    return list(theme.THEMES.values())


def interactive(t: theme.Theme) -> list[tuple[str, str]]:
    """The colours a theme actually paints its interactive parts in.

    A theme with its own fixed colour has an empty `accents` list, but that
    colour still lands on the export button and in the selection duration, so
    it faces exactly the same measurements as one of eleven choices would.
    """
    return list(t.accents) or [(t.name, t.default_accent)]


class Palette(unittest.TestCase):
    """Default's eleven. Cyberpunk deliberately has none -- see Themes."""

    def test_offers_a_default_plus_ten(self):
        self.assertEqual(len(theme.ACCENTS), 11)

    def test_the_default_is_apricot(self):
        self.assertEqual(theme.DEFAULT_ACCENT, "#e27125")
        self.assertIn(("Apricot", "#e27125"), theme.ACCENTS)

    def test_the_default_is_among_the_choices(self):
        self.assertIn(theme.DEFAULT_ACCENT, [c for _, c in theme.ACCENTS])

    def test_every_entry_is_a_usable_colour(self):
        for t in every_theme():
            for name, colour in interactive(t):
                with self.subTest(theme=t.key, accent=name):
                    self.assertRegex(colour, r"^#[0-9a-f]{6}$",
                                     f"{name} is not lowercase hex")
                    self.assertTrue(theme.is_valid(colour))

    def test_names_and_colours_are_unique(self):
        for t in every_theme():
            with self.subTest(theme=t.key):
                names = [n for n, _ in interactive(t)]
                colours = [c for _, c in interactive(t)]
                self.assertEqual(len(set(names)), len(names))
                self.assertEqual(len(set(colours)), len(colours))

    def test_every_theme_defaults_to_a_colour_it_offers(self):
        for t in every_theme():
            with self.subTest(theme=t.key):
                self.assertIn(t.default_accent, [c for _, c in interactive(t)])


class Readability(unittest.TestCase):
    def test_every_accent_is_readable_on_the_window(self):
        for t in every_theme():
            for name, colour in interactive(t):
                ratio = theme.contrast(colour, t.background)
                with self.subTest(theme=t.key, accent=name):
                    self.assertGreaterEqual(
                        ratio, MIN_CONTRAST,
                        f"{name} ({colour}) is only {ratio:.2f}:1 on {t.key}")

    def test_every_accent_is_readable_on_the_sunken_surface(self):
        # Text fields use a darker fill; the focus ring and selection sit there.
        for t in every_theme():
            for name, colour in interactive(t):
                with self.subTest(theme=t.key, accent=name):
                    self.assertGreaterEqual(
                        theme.contrast(colour, t.sunken), MIN_CONTRAST,
                        f"{name} on {t.key}'s sunken surface")

    def test_body_text_is_readable_on_every_surface(self):
        # Not the accent's problem, but a theme could get this wrong on its own
        # and nothing else here would notice.
        for t in every_theme():
            for surface in ("background", "surface", "sunken", "popup_bg"):
                for ink in ("text", "text_dim"):
                    ratio = theme.contrast(getattr(t, ink), getattr(t, surface))
                    with self.subTest(theme=t.key, ink=ink, on=surface):
                        self.assertGreaterEqual(
                            ratio, MIN_CONTRAST,
                            f"{t.key} {ink} on {surface} is {ratio:.2f}:1")

    def test_text_on_the_accent_is_readable(self):
        # The export button puts text on top of the accent.
        for t in every_theme():
            for name, colour in interactive(t):
                ratio = theme.contrast(colour, theme.on_accent(colour))
                with self.subTest(theme=t.key, accent=name):
                    self.assertGreaterEqual(
                        ratio, MIN_CONTRAST,
                        f"button text on {name} is {ratio:.2f}:1")

    def test_on_accent_chooses_dark_text_for_a_light_colour(self):
        self.assertNotEqual(theme.on_accent("#f5d76e"), "#ffffff")

    def test_on_accent_chooses_light_text_for_a_dark_colour(self):
        # Not in any shipped palette, but the helper must not assume.
        self.assertEqual(theme.on_accent("#1b2a6b"), "#ffffff")

    def test_accents_are_telling_apart_from_each_other(self):
        for t in every_theme():
            for (a, first), (b, second) in itertools.combinations(interactive(t), 2):
                gap = distance(first, second)
                with self.subTest(theme=t.key, pair=(a, b)):
                    self.assertGreaterEqual(
                        gap, MIN_DISTANCE, f"{a} and {b} are only dE {gap:.1f} apart")

    def test_accents_do_not_impersonate_the_semantic_colours(self):
        # Green means "this export is a lossless copy" and red means "this will
        # delete your original". An accent close enough to be mistaken for
        # either would make the interface lie -- in any theme, against that
        # theme's own two colours.
        for t in every_theme():
            for name, colour in interactive(t):
                with self.subTest(theme=t.key, accent=name):
                    self.assertGreaterEqual(
                        distance(colour, t.lossless), MIN_DISTANCE,
                        f"{name} could be mistaken for {t.key}'s lossless badge")
                    self.assertGreaterEqual(
                        distance(colour, t.destructive_text), MIN_DISTANCE,
                        f"{name} could be mistaken for {t.key}'s delete warning")

    def test_the_two_statements_never_look_alike(self):
        # "Lossless copy" and "this deletes your original" are the two the user
        # must never confuse, whatever else a theme does.
        for t in every_theme():
            with self.subTest(theme=t.key):
                self.assertGreaterEqual(
                    distance(t.lossless, t.destructive), MIN_DISTANCE)
                self.assertGreaterEqual(
                    distance(t.lossless, t.destructive_text), MIN_DISTANCE)


class Themes(unittest.TestCase):
    def test_the_two_shipped_themes(self):
        self.assertEqual(list(theme.THEMES), ["default", "cyberpunk"])

    def test_keys_and_names_are_unique_and_match_their_entry(self):
        for key, t in theme.THEMES.items():
            with self.subTest(theme=key):
                self.assertEqual(key, t.key)
        names = [t.name for t in every_theme()]
        self.assertEqual(len(set(names)), len(names))

    def test_get_falls_back_rather_than_crashing(self):
        # Same contract as the accent: a settings file is user-editable and
        # survives upgrades, so anything can turn up here.
        for junk in ("", "  ", "nonsense", None, 42, [], "Default "):
            with self.subTest(stored=junk):
                self.assertIs(theme.get(junk), theme.DEFAULT)

    def test_get_is_case_insensitive(self):
        self.assertIs(theme.get("CYBERPUNK"), theme.CYBERPUNK)
        self.assertIs(theme.get(" cyberpunk "), theme.CYBERPUNK)

    def test_a_theme_with_no_palette_keeps_its_own_colour(self):
        # Switching away from Default must not drag an apricot into Night City.
        for offered in ("#e27125", "#3d92e0", "#ffffff", None, "junk"):
            with self.subTest(offered=offered):
                self.assertEqual(theme.CYBERPUNK.normalise(offered),
                                 theme.CYBERPUNK.default_accent)

    def test_a_theme_with_a_palette_accepts_a_choice(self):
        self.assertEqual(theme.DEFAULT.normalise("#3d92e0"), "#3d92e0")

    def test_cyberpunk_is_the_red(self):
        # Pinned deliberately: the rest of the suite follows whatever the theme
        # says, so without this the palette could drift to anything and still
        # look correct to every other test.
        self.assertEqual(theme.CYBERPUNK.default_accent, "#ff0056")

    def test_only_default_offers_a_picker(self):
        self.assertTrue(theme.DEFAULT.accents)
        self.assertFalse(theme.CYBERPUNK.accents)

    def test_the_font_stack_always_ends_somewhere_real(self):
        # A bundled face that failed to load must degrade, not disappear.
        self.assertEqual(theme.font_stack(theme.DEFAULT), "")
        stack = theme.font_stack(theme.CYBERPUNK)
        self.assertTrue(stack.startswith("Rajdhani,"))
        self.assertTrue(stack.rstrip().endswith("sans-serif"))


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
        for t in every_theme():
            for name, colour in interactive(t):
                with self.subTest(theme=t.key, accent=name):
                    self.assertLess(
                        theme.contrast(theme.muted(colour, t), t.background),
                        theme.contrast(colour, t.background),
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
        for t in every_theme():
            for name, colour in interactive(t):
                with self.subTest(theme=t.key, accent=name):
                    self.assertIn(colour, theme.stylesheet(t, colour))

    def test_the_previous_accent_does_not_linger(self):
        sheet = theme.stylesheet(theme.DEFAULT, "#3d92e0")
        self.assertNotIn("#e27125", sheet)

    def test_an_invalid_accent_still_produces_a_sheet(self):
        self.assertIn(theme.DEFAULT_ACCENT,
                      theme.stylesheet(theme.DEFAULT, "nonsense"))

    def test_a_colour_in_the_theme_slot_is_read_as_the_accent(self):
        # The accent used to be the first argument. Quietly returning a
        # default-accented sheet would be a bug with no symptom.
        self.assertIn("#e8a33d", theme.stylesheet("#e8a33d"))

    def test_a_theme_key_works_as_well_as_a_theme(self):
        self.assertEqual(theme.stylesheet("cyberpunk"),
                         theme.stylesheet(theme.CYBERPUNK))

    def test_an_invalid_theme_still_produces_a_sheet(self):
        self.assertEqual(theme.stylesheet("nonsense"), theme.stylesheet(theme.DEFAULT))

    def test_it_styles_the_controls_that_matter(self):
        for t in every_theme():
            sheet = theme.stylesheet(t)
            for selector in ("QPushButton#primary", "QLineEdit:focus", "QLabel#badge",
                             "QProgressBar::chunk", "QPushButton#swatch",
                             "QComboBox QAbstractItemView", "QLabel#clock"):
                with self.subTest(theme=t.key, selector=selector):
                    self.assertIn(selector, sheet, f"{selector} lost its styling")

    def test_semantic_colours_ignore_the_accent(self):
        # Whatever the accent, "lossless" stays green and "delete" stays red.
        for t in every_theme():
            for name, colour in interactive(t):
                sheet = theme.stylesheet(t, colour)
                with self.subTest(theme=t.key, accent=name):
                    self.assertIn(t.lossless, sheet)
                    self.assertIn(t.destructive, sheet)

    def test_a_theme_does_not_leak_another_theme_s_surfaces(self):
        # The failure this catches is a rule left un-parameterised, which shows
        # up as one charcoal panel in an otherwise black window.
        sheet = theme.stylesheet(theme.CYBERPUNK)
        for field in ("background", "surface", "sunken", "border",
                      "text", "text_dim", "popup_bg"):
            other = getattr(theme.DEFAULT, field)
            if other in {getattr(theme.CYBERPUNK, f) for f in
                         ("background", "surface", "sunken", "border",
                          "text", "text_dim", "popup_bg")}:
                continue
            with self.subTest(field=field):
                self.assertNotIn(other, sheet,
                                 f"Default's {field} ({other}) survived into Cyberpunk")

    def test_no_placeholder_survives_formatting(self):
        # A missed brace would silently ship "{a}" into the sheet, and Qt would
        # drop the whole rule rather than complain.
        for t in every_theme():
            with self.subTest(theme=t.key):
                self.assertNotRegex(theme.stylesheet(t), r"\{\s*[a-z_]+\s*\}",
                                    "an f-string field was left unfilled")

    def test_every_declared_colour_is_a_real_colour(self):
        # Qt ignores rules it cannot parse, so a typo'd hex disappears quietly.
        for t in every_theme():
            for value in re.findall(r":\s*(#[0-9a-fA-F]+)", theme.stylesheet(t)):
                with self.subTest(theme=t.key, value=value):
                    self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", f"{value} is malformed")

    def test_a_chamfered_theme_gets_out_of_the_way_of_its_painter(self):
        # chrome.ChamferButton draws the polygon itself. If the sheet also
        # painted a rounded rect, a corner of it would show through the notch.
        sheet = theme.stylesheet(theme.CYBERPUNK)
        self.assertIn("background: transparent", sheet)
        self.assertNotIn("border-radius: 5px", sheet)

    def test_the_default_theme_still_rounds_its_corners(self):
        self.assertIn("border-radius: 5px", theme.stylesheet(theme.DEFAULT))
        self.assertNotIn("background: transparent", theme.stylesheet(theme.DEFAULT))


if __name__ == "__main__":
    unittest.main()
