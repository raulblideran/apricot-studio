"""Widget tests.

The Timeline is tested headlessly: it paints itself and owns no video surface,
so it runs fine under the offscreen platform. The main window does not --
QVideoWidget needs a real GL surface and aborts offscreen -- so the tests that
need the whole window are skipped unless a display is actually present.
"""

from __future__ import annotations

import os
import unittest

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QLineEdit

import timeline as timeline_mod
from timeline import Timeline

_app = QApplication.instance() or QApplication([])

# Whether a window can genuinely be shown and focused. The environment variables
# alone are not enough: QT_QPA_PLATFORM=offscreen can be forced while a display
# exists, and under offscreen nothing ever becomes the active window, so focus
# assertions would report nonsense rather than fail honestly.
REAL_WINDOWS = (_app.platformName() != "offscreen"
                and bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")))

def dispose(window) -> None:
    """Release a window deterministically.

    Two Clipper instances surviving to interpreter shutdown segfault: their
    media stacks get torn down after Qt has partly gone, in arbitrary order.
    Dropping each one while the event loop is still healthy avoids that.
    """
    import gc
    window.close()
    window.deleteLater()
    _app.processEvents()
    gc.collect()
    _app.processEvents()


DURATION = 90.0
KEYFRAMES = [i * 2.0 for i in range(46)]


def make_timeline(width=1000, height=112) -> Timeline:
    widget = Timeline()
    widget.resize(width, height)
    widget.reset(DURATION)
    widget.set_keyframes(KEYFRAMES)
    return widget


class Geometry(unittest.TestCase):
    def setUp(self):
        self.tl = make_timeline()

    def test_time_and_pixels_round_trip(self):
        for t in (0.0, 12.5, 45.0, 89.9):
            self.assertAlmostEqual(self.tl._time_at(self.tl._x_of(t)), t, places=3)

    def test_round_trips_while_zoomed(self):
        self.tl._set_view(40.0, 50.0)
        for t in (40.5, 45.0, 49.5):
            self.assertAlmostEqual(self.tl._time_at(self.tl._x_of(t)), t, places=3)

    def test_clamps_to_the_file(self):
        self.assertEqual(self.tl._time_at(-500), 0.0)
        self.assertEqual(self.tl._time_at(99999), DURATION)

    def test_empty_timeline_does_not_divide_by_zero(self):
        blank = Timeline()
        blank.resize(400, 112)
        blank.reset(0.0)
        blank._x_of(0.0)
        self.assertEqual(blank._time_at(200), 0.0)


class Selection(unittest.TestCase):
    def setUp(self):
        self.tl = make_timeline()

    def test_starts_covering_the_whole_file(self):
        self.assertEqual(self.tl.in_point, 0.0)
        self.assertEqual(self.tl.out_point, DURATION)

    def test_marks_clamp_to_the_file(self):
        self.tl.set_in(-10.0)
        self.assertEqual(self.tl.in_point, 0.0)
        self.tl.set_out(1e6)
        self.assertEqual(self.tl.out_point, DURATION)

    def test_in_cannot_pass_out(self):
        self.tl.set_out(30.0)
        self.tl.set_in(50.0)
        self.assertLessEqual(self.tl.in_point, self.tl.out_point)

    def test_out_cannot_precede_in(self):
        self.tl.set_in(50.0)
        self.tl.set_out(10.0)
        self.assertLessEqual(self.tl.in_point, self.tl.out_point)

    def test_playhead_clamps(self):
        self.tl.set_position(1e6)
        self.assertEqual(self.tl._position, DURATION)
        self.tl.set_position(-5)
        self.assertEqual(self.tl._position, 0.0)


class Snapping(unittest.TestCase):
    """Landing on a keyframe is what turns an export into an instant copy."""

    def setUp(self):
        self.tl = make_timeline()

    def test_reports_when_the_in_point_sits_on_a_keyframe(self):
        self.tl.set_in(44.0)
        self.assertTrue(self.tl.snapped)

    def test_reports_when_it_does_not(self):
        self.tl.set_in(45.0)
        self.assertFalse(self.tl.snapped)

    def test_pulls_a_nearby_drag_onto_a_keyframe(self):
        near = 44.0 + self.tl.view_span / self.tl._track_width()   # one pixel off
        value, snapped = self.tl._snap_in(near, disable=False)
        self.assertTrue(snapped)
        self.assertAlmostEqual(value, 44.0, places=6)

    def test_leaves_distant_drags_alone(self):
        value, snapped = self.tl._snap_in(45.0, disable=False)
        self.assertFalse(snapped)
        self.assertEqual(value, 45.0)

    def test_alt_overrides_the_magnetism(self):
        near = 44.0 + self.tl.view_span / self.tl._track_width()
        value, snapped = self.tl._snap_in(near, disable=True)
        self.assertFalse(snapped)
        self.assertEqual(value, near)

    def test_snapping_is_finer_when_zoomed_in(self):
        # Tolerance is a pixel distance, so zooming in must tighten it in seconds.
        wide, _ = self.tl._snap_in(44.4, disable=False)
        self.tl._set_view(43.0, 45.0)
        tight, _ = self.tl._snap_in(44.4, disable=False)
        self.assertEqual(wide, 44.0, "should snap when a pixel covers a lot of time")
        self.assertEqual(tight, 44.4, "should not snap once pixels are precise")

    def test_no_keyframes_means_no_snapping(self):
        bare = Timeline()
        bare.resize(1000, 112)
        bare.reset(DURATION)
        value, snapped = bare._snap_in(44.4, disable=False)
        self.assertFalse(snapped)
        self.assertEqual(value, 44.4)


class Zoom(unittest.TestCase):
    def setUp(self):
        self.tl = make_timeline()

    def test_starts_showing_everything(self):
        self.assertFalse(self.tl.zoomed)
        self.assertAlmostEqual(self.tl.view_span, DURATION)

    def test_framing_the_selection_narrows_the_view(self):
        self.tl.set_in(40.0)
        self.tl.set_out(50.0)
        self.tl.zoom_to_selection()
        self.assertTrue(self.tl.zoomed)
        self.assertLess(self.tl.view_span, DURATION)
        self.assertGreaterEqual(self.tl.view_span, 10.0, "selection must still fit")

    def test_zoom_all_restores(self):
        self.tl._set_view(40.0, 50.0)
        self.tl.zoom_all()
        self.assertFalse(self.tl.zoomed)
        self.assertAlmostEqual(self.tl.view_span, DURATION)

    def test_cannot_zoom_past_the_minimum(self):
        for _ in range(60):
            self.tl.zoom_in()
        self.assertGreaterEqual(self.tl.view_span, timeline_mod.MIN_SPAN - 1e-9)

    def test_cannot_zoom_out_past_the_file(self):
        for _ in range(60):
            self.tl.zoom_out()
        self.assertLessEqual(self.tl.view_span, DURATION + 1e-9)

    def test_view_never_escapes_the_file(self):
        self.tl._set_view(-50.0, 10.0)
        self.assertGreaterEqual(self.tl._view_start, 0.0)
        self.tl._set_view(DURATION - 2, DURATION + 50)
        self.assertLessEqual(self.tl._view_end, DURATION + 1e-6)

    def test_zooming_keeps_the_anchor_under_the_cursor(self):
        self.tl._zoom_at(0.5, 45.0)
        self.assertLess(self.tl._view_start, 45.0)
        self.assertGreater(self.tl._view_end, 45.0)


class Filmstrip(unittest.TestCase):
    def setUp(self):
        self.tl = make_timeline()

    def _add_thumbs(self, n):
        for _ in range(n):
            self.tl.add_thumbnail(QImage(timeline_mod.THUMB_W, timeline_mod.THUMB_H,
                                         QImage.Format.Format_RGB888))

    def test_frames_are_placed_at_their_keyframe_times(self):
        # The filmstrip decodes keyframes only, so frame i belongs at keyframe i.
        self._add_thumbs(len(KEYFRAMES))
        self.assertAlmostEqual(self.tl._thumb_time(0), 0.0)
        self.assertAlmostEqual(self.tl._thumb_time(10), 20.0)

    def test_falls_back_to_even_spacing_on_a_count_mismatch(self):
        # Loaders finish independently; a mismatch must not throw.
        self._add_thumbs(5)
        self.assertAlmostEqual(self.tl._thumb_time(0), 0.0)
        self.assertAlmostEqual(self.tl._thumb_time(4), DURATION)

    def test_hover_finds_the_nearest_frame(self):
        self._add_thumbs(len(KEYFRAMES))
        self.assertIsNotNone(self.tl._nearest_thumb(45.0))

    def test_hover_is_safe_before_any_frames_arrive(self):
        self.assertIsNone(self.tl._nearest_thumb(45.0))


class Painting(unittest.TestCase):
    """The widget must render without throwing in every state it can be in."""

    def _render(self, tl):
        tl.grab()          # forces a full paintEvent

    def test_paints_while_empty(self):
        blank = Timeline()
        blank.resize(800, 112)
        blank.reset(0.0)
        self._render(blank)

    def test_paints_with_every_layer_present(self):
        tl = make_timeline()
        tl.set_peaks([abs((i % 40) - 20) / 20 for i in range(2000)])
        for _ in range(len(KEYFRAMES)):
            tl.add_thumbnail(QImage(timeline_mod.THUMB_W, timeline_mod.THUMB_H,
                                    QImage.Format.Format_RGB888))
        tl.set_in(20.0)
        tl.set_out(40.0)
        tl.set_position(30.0)
        self._render(tl)

    def test_paints_while_zoomed_with_marks_offscreen(self):
        tl = make_timeline()
        tl.set_in(5.0)
        tl.set_out(85.0)
        tl._set_view(40.0, 50.0)      # both handles now outside the view
        self._render(tl)

    def test_paints_the_hover_preview(self):
        tl = make_timeline()
        tl.add_thumbnail(QImage(timeline_mod.THUMB_W, timeline_mod.THUMB_H,
                                QImage.Format.Format_RGB888))
        tl._hover_x = 400.0
        self._render(tl)

    def test_paints_at_an_awkward_size(self):
        tl = make_timeline(width=40, height=112)
        tl.set_peaks([0.5] * 2000)
        self._render(tl)


@unittest.skipUnless(REAL_WINDOWS, "needs a real display; focus is meaningless offscreen")
class MainWindow(unittest.TestCase):
    """Regression cover for Space typing into a field instead of playing."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtTest import QTest
        from clipper import Clipper
        from tests import fixtures
        cls.window = Clipper()
        cls.window.show()
        cls.window.activateWindow()
        cls.window.raise_()
        QTest.qWaitForWindowActive(cls.window, 3000)
        # The fields are disabled until something is open, and a disabled widget
        # cannot hold focus -- which is the state the focus bug lived in.
        cls.window.load(fixtures.sample("allp"))

    @classmethod
    def tearDownClass(cls):
        cls.window._close_source()      # stops the background ffmpeg loaders
        dispose(cls.window)
        cls.window = None

    def held_by_a_field(self) -> bool:
        return isinstance(self.window.focusWidget(), QLineEdit)

    def test_a_click_outside_releases_a_text_field(self):
        self.window._in_edit.setFocus()
        self.assertTrue(self.held_by_a_field())
        self.window._release_text_focus()
        self.assertFalse(self.held_by_a_field())

    def test_dropdowns_and_buttons_never_take_focus(self):
        # They swallow the click, so if they also took focus the field would keep
        # the keyboard and Space would type into it.
        for widget in (self.window._fmt_box, self.window._audio_box,
                       self.window._size_box, self.window._export_btn,
                       self.window._play_btn):
            self.assertEqual(widget.focusPolicy(), Qt.FocusPolicy.NoFocus,
                             f"{widget.objectName() or widget} would steal focus")

    def test_the_timeline_takes_focus_so_clicking_it_frees_the_field(self):
        self.assertEqual(self.window._timeline.focusPolicy(),
                         Qt.FocusPolicy.ClickFocus)

    def test_video_surface_also_releases_the_field(self):
        self.assertEqual(self.window._video.focusPolicy(),
                         Qt.FocusPolicy.ClickFocus)

    def test_loading_enables_the_controls(self):
        for widget in (self.window._export_btn, self.window._fmt_box,
                       self.window._delete_source):
            self.assertTrue(widget.isEnabled())


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class DeleteTargetsTheRightFile(unittest.TestCase):
    """Regression cover for a delete prompt that offered the wrong file.

    Opening another video while an export encodes used to repoint the prompt at
    whatever was loaded when the encode landed, so confirming it would have
    destroyed a file that was never cut.
    """

    def setUp(self):
        import shutil
        import tempfile
        from clipper import Clipper
        from tests import fixtures
        self.tmp = tempfile.mkdtemp(prefix="clipper-race-")
        self.cut = os.path.join(self.tmp, "cut_this.mp4")
        self.other = os.path.join(self.tmp, "do_not_touch.mp4")
        shutil.copy(fixtures.sample("allp"), self.cut)
        shutil.copy(fixtures.sample("allp"), self.other)
        self.clip = os.path.join(self.tmp, "made.mp4")
        self.window = Clipper()
        self.window.show()
        self.window.load(self.cut)

    def tearDown(self):
        import shutil
        self.window._close_source()
        dispose(self.window)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_source_is_captured_when_the_export_starts(self):
        self.window._timeline.set_in(0.5)
        self.window._timeline.set_out(2.0)
        self.window._sync_marks()
        self.window._out_path.setText(os.path.join(self.tmp, "clip.mp4"))
        self.window.start_export()
        recorded = self.window._exported
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.source, self.cut)

        # Now open something else, exactly as a user waiting on an encode might.
        self.window.load(self.other)
        self.assertEqual(self.window._info.path, self.other)
        self.assertEqual(self.window._exported.source, self.cut,
                         "the delete prompt must follow the export, not the window")
        self.window._exporter.cancel()

    def candidate(self, **over):
        """What the delete prompt would actually target for a given record."""
        from clipper import Clipper, Exported
        fields = dict(source=self.cut, output=self.clip, duration=6.0, kept=2.0)
        fields.update(over)
        return Clipper._delete_candidate(Exported(**fields))

    def write_clip(self, size=8192):
        self.clip = os.path.join(self.tmp, "made.mp4")
        with open(self.clip, "wb") as handle:
            handle.write(b"\0" * size)
        return self.clip

    def test_it_targets_the_recorded_source_not_the_open_file(self):
        # The mutation that matters: reading self._info here would offer to
        # delete whatever happens to be loaded when the encode lands.
        self.write_clip()
        self.window.load(self.other)
        self.assertEqual(self.window._info.path, self.other)
        source, _ = self.candidate()
        self.assertEqual(source, self.cut)

    def test_it_declines_when_the_clip_was_never_written(self):
        self.clip = os.path.join(self.tmp, "missing.mp4")
        self.assertIsNone(self.candidate())

    def test_it_declines_when_the_clip_is_suspiciously_small(self):
        self.write_clip(size=10)
        self.assertIsNone(self.candidate(),
                          "a near-empty export must never justify a deletion")

    def test_it_declines_when_the_source_has_already_gone(self):
        self.write_clip()
        self.assertIsNone(self.candidate(source=os.path.join(self.tmp, "gone.mp4")))

    def test_it_declines_when_the_clip_is_the_source(self):
        self.write_clip()
        self.assertIsNone(self.candidate(output=self.cut))

    def test_it_declines_without_a_record(self):
        from clipper import Clipper
        self.assertIsNone(Clipper._delete_candidate(None))

    def test_the_record_is_consumed_so_it_cannot_fire_twice(self):
        self.window._timeline.set_in(0.5)
        self.window._timeline.set_out(2.0)
        self.window._sync_marks()
        self.window._out_path.setText(os.path.join(self.tmp, "clip2.mp4"))
        self.window.start_export()
        self.window._exporter.cancel()
        # A cancelled export writes nothing, so the prompt must decline and the
        # record must not survive to be acted on later.
        self.window._maybe_delete_source()
        self.assertIsNone(self.window._exported)
        self.assertTrue(os.path.exists(self.cut))


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class EmptyWindow(unittest.TestCase):
    """A freshly opened window, before any file is chosen."""

    @classmethod
    def setUpClass(cls):
        from clipper import Clipper
        cls.window = Clipper()

    @classmethod
    def tearDownClass(cls):
        dispose(cls.window)
        cls.window = None

    def test_nothing_is_enabled_before_a_file_is_loaded(self):
        for widget in (self.window._export_btn, self.window._fmt_box,
                       self.window._delete_source, self.window._in_edit):
            self.assertFalse(widget.isEnabled())

    def test_delete_option_starts_disarmed(self):
        self.assertFalse(self.window._delete_source.isChecked(),
                         "a destructive option must never default to on")

    def test_no_badge_without_a_file(self):
        self.assertEqual(self.window._badge.text(), "")


if __name__ == "__main__":
    unittest.main()
