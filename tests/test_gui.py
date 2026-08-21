"""Widget tests.

The Timeline is tested headlessly: it paints itself and owns no video surface,
so it runs fine under the offscreen platform. The main window does not --
QVideoWidget needs a real GL surface and aborts offscreen -- so the tests that
need the whole window are skipped unless a display is actually present.
"""

from __future__ import annotations

import errno
import os
import unittest
import unittest.mock       # 3.14 no longer pulls this in with unittest itself

from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QLineEdit, QMessageBox

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

    Two ApricotStudio instances surviving to interpreter shutdown segfault: their
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


class FollowingThePlayhead(unittest.TestCase):
    """Zoomed in, playback used to run off the edge with the view standing still."""

    def setUp(self):
        self.tl = make_timeline()
        self.tl._set_view(40.0, 50.0)

    def test_the_view_moves_when_playback_leaves_it(self):
        self.tl.set_position(52.0)
        self.assertLessEqual(self.tl._view_start, 52.0)
        self.assertGreaterEqual(self.tl._view_end, 52.0)

    def test_it_pages_rather_than_centres(self):
        # The playhead should land near the left, so there is a screenful of
        # file ahead of it rather than half of one.
        self.tl.set_position(52.0)
        into = (52.0 - self.tl._view_start) / self.tl.view_span
        self.assertLess(into, 0.35)

    def test_jumping_backwards_leaves_room_behind(self):
        self.tl.set_position(20.0)
        into = (20.0 - self.tl._view_start) / self.tl.view_span
        self.assertGreater(into, 0.5)

    def test_the_span_is_unchanged(self):
        before = self.tl.view_span
        self.tl.set_position(52.0)
        self.assertAlmostEqual(self.tl.view_span, before, places=6)

    def test_it_stays_put_while_the_view_still_holds_the_playhead(self):
        self.tl.set_position(45.0)
        self.assertAlmostEqual(self.tl._view_start, 40.0)

    def test_it_does_not_fight_a_drag(self):
        # Scrubbing sets the position on every mouse move; paging under the
        # pointer would drag the file out from under it.
        self.tl._drag = "seek"
        self.tl.set_position(52.0)
        self.assertAlmostEqual(self.tl._view_start, 40.0)

    def test_an_unzoomed_timeline_never_moves(self):
        tl = make_timeline()
        tl.set_position(80.0)
        self.assertAlmostEqual(tl._view_start, 0.0)
        self.assertAlmostEqual(tl.view_span, DURATION)


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


class ALongRecording(unittest.TestCase):
    """A two-hour file has thousands of keyframes; the strip must not hold them all."""

    def setUp(self):
        self.tl = Timeline()
        self.tl.resize(1000, 112)
        self.tl.reset(7200.0)
        self.frames = 2000
        self.tl.set_keyframes([i * 3.6 for i in range(self.frames)])
        for _ in range(self.frames):
            self.tl.add_thumbnail(QImage(timeline_mod.THUMB_W, timeline_mod.THUMB_H,
                                         QImage.Format.Format_RGB888))

    def test_the_strip_stops_growing(self):
        self.assertLessEqual(len(self.tl._thumbs), timeline_mod.MAX_THUMBS)

    def test_it_still_knows_when_each_kept_frame_was_taken(self):
        # Thinning must widen the interval each frame stands for, not shift the
        # strip out of step with the keyframes it was decoded from.
        self.assertAlmostEqual(self.tl._thumb_time(0), 0.0)
        last = len(self.tl._thumbs) - 1
        self.assertAlmostEqual(self.tl._thumb_time(last),
                               (last * self.tl._thumb_stride) * 3.6)

    def test_the_strip_still_spans_the_file(self):
        self.assertGreater(self.tl._thumb_time(len(self.tl._thumbs) - 1),
                           7200.0 * 0.9)

    def test_it_paints(self):
        self.tl.grab()

    def test_reset_starts_over_at_full_resolution(self):
        self.tl.reset(60.0)
        self.assertEqual(self.tl._thumb_stride, 1)
        self.assertEqual(self.tl._thumbs_seen, 0)
        self.assertEqual(self.tl._thumbs, [])


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
        from apricot import ApricotStudio
        from tests import fixtures
        cls.window = ApricotStudio()
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
        from apricot import ApricotStudio
        from tests import fixtures
        self.tmp = tempfile.mkdtemp(prefix="apricot-race-")
        self.cut = os.path.join(self.tmp, "cut_this.mp4")
        self.other = os.path.join(self.tmp, "do_not_touch.mp4")
        shutil.copy(fixtures.sample("allp"), self.cut)
        shutil.copy(fixtures.sample("allp"), self.other)
        self.clip = os.path.join(self.tmp, "made.mp4")
        self.window = ApricotStudio()
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
        self.window._out_dir.setText(self.tmp)
        self.window._out_name.setText("clip")
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
        from apricot import ApricotStudio, Exported
        fields = dict(source=self.cut, output=self.clip, duration=6.0, kept=2.0)
        fields.update(over)
        return ApricotStudio._delete_candidate(Exported(**fields))

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
        from apricot import ApricotStudio
        self.assertIsNone(ApricotStudio._delete_candidate(None))

    def test_the_record_is_consumed_so_it_cannot_fire_twice(self):
        self.window._timeline.set_in(0.5)
        self.window._timeline.set_out(2.0)
        self.window._sync_marks()
        self.window._out_dir.setText(self.tmp)
        self.window._out_name.setText("clip2")
        self.window.start_export()
        self.window._exporter.cancel()
        # A cancelled export writes nothing, so the prompt must decline and the
        # record must not survive to be acted on later.
        self.window._maybe_delete_source()
        self.assertIsNone(self.window._exported)
        self.assertTrue(os.path.exists(self.cut))


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class OutputNaming(unittest.TestCase):
    """Folder and name are separate, and the name is the user's to keep."""

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from apricot import ApricotStudio
        from tests import fixtures
        cls.tmp = tempfile.mkdtemp(prefix="apricot-name-")
        cls.source = os.path.join(cls.tmp, "replay.mp4")
        shutil.copy(fixtures.sample("allp"), cls.source)
        cls.window = ApricotStudio()
        cls.window.show()
        cls.window.load(cls.source)

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.window._close_source()
        dispose(cls.window)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.window.load(self.source)
        self.window._fmt_box.setCurrentIndex(0)

    def test_loading_suggests_a_folder_and_a_name(self):
        self.assertEqual(self.window._out_dir.text(), self.tmp)
        self.assertEqual(self.window._out_name.text(), "replay_clip")
        self.assertEqual(self.window._out_ext.text(), ".mp4")

    def test_the_two_fields_recombine_into_a_path(self):
        self.window._out_dir.setText("/tmp/somewhere")
        self.window._out_name.setText("trickshot")
        self.assertEqual(self.window._output_path(), "/tmp/somewhere/trickshot.mp4")

    def test_changing_format_keeps_the_name_and_swaps_the_extension(self):
        self.window._out_name.setText("trickshot")
        for index, ext in ((1, ".webm"), (2, ".gif"), (0, ".mp4")):
            self.window._fmt_box.setCurrentIndex(index)
            self.assertEqual(self.window._out_name.text(), "trickshot",
                             "the format changed the user's chosen name")
            self.assertEqual(self.window._out_ext.text(), ext)
            self.assertTrue(self.window._output_path().endswith(f"trickshot{ext}"))

    def test_an_extension_typed_into_the_name_is_not_doubled(self):
        self.window._out_name.setText("clip.mp4")
        self.assertTrue(self.window._output_path().endswith("clip.mp4"))
        self.assertNotIn(".mp4.mp4", self.window._output_path())

    def test_an_unrelated_dot_in_the_name_survives(self):
        self.window._out_name.setText("round 2.5")
        self.assertTrue(self.window._output_path().endswith("round 2.5.mp4"))

    def test_an_empty_field_yields_no_path(self):
        self.window._out_name.setText("")
        self.assertEqual(self.window._output_path(), "")
        self.window._out_name.setText("x")
        self.window._out_dir.setText("")
        self.assertEqual(self.window._output_path(), "")

    def test_a_home_relative_folder_is_expanded(self):
        self.window._out_dir.setText("~/Videos")
        self.window._out_name.setText("clip")
        self.assertTrue(self.window._output_path().startswith(os.path.expanduser("~")))
        self.assertNotIn("~", self.window._output_path())

    def finish_a_pretend_export(self, name="trickshot"):
        """Drive the post-export path without waiting on a real encode."""
        from apricot import Exported
        self.window._out_name.setText(name)
        output = self.window._output_path()
        open(output, "w").close()
        self.window._exported = Exported(source=self.source, output=output,
                                         duration=6.0, kept=2.0)
        self.window._on_export_finished(True, f"Saved {os.path.basename(output)}")
        return output

    def test_the_name_survives_a_render(self):
        # Regenerating a name here would throw away a deliberate choice, and the
        # user would have to retype it for every clip.
        output = self.finish_a_pretend_export("trickshot")
        self.assertEqual(self.window._out_name.text(), "trickshot")
        self.assertEqual(self.window._out_dir.text(), self.tmp)
        os.remove(output)

    def test_the_folder_survives_a_render_too(self):
        self.window._out_dir.setText(self.tmp)
        output = self.finish_a_pretend_export("keepme")
        self.assertEqual(self.window._out_dir.text(), self.tmp)
        os.remove(output)

    def test_a_finished_export_is_remembered_for_reveal(self):
        output = self.finish_a_pretend_export("revealme")
        self.assertEqual(self.window._last_output, output)
        self.assertTrue(self.window._reveal_btn.isVisible())
        os.remove(output)

    def test_the_suggested_name_avoids_an_existing_file(self):
        open(os.path.join(self.tmp, "replay_clip.mp4"), "w").close()
        self.window.load(self.source)
        self.assertEqual(self.window._out_name.text(), "replay_clip2")
        os.remove(os.path.join(self.tmp, "replay_clip.mp4"))


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class AccentColour(unittest.TestCase):
    """Picking an accent has to reach both the stylesheet and the timeline.

    The timeline paints itself rather than being styled by Qt, so it is the
    half that silently keeps the old colour if only the sheet is updated.
    """

    def setUp(self):
        import theme
        from apricot import ApricotStudio
        self.theme = theme
        self.window = ApricotStudio()
        self.window.show()
        self.addCleanup(self._restore)

    def _restore(self):
        self.window._apply_accent(self.theme.DEFAULT_ACCENT)
        dispose(self.window)

    def test_starts_on_apricot(self):
        self.assertEqual(self.window._accent, "#e27125")

    def test_choosing_one_repaints_the_timeline_too(self):
        self.window._apply_accent("#3d92e0")
        self.assertEqual(self.window._timeline._accent.name(), "#3d92e0")

    def test_choosing_one_restyles_the_window(self):
        self.window._apply_accent("#26a69a")
        self.assertIn("#26a69a", self.window.styleSheet())
        self.assertNotIn("#e27125", self.window.styleSheet())

    def test_the_handle_grip_stays_legible(self):
        # Drawn on top of the accent, so it cannot be a fixed colour.
        for _, colour in self.theme.ACCENTS:
            self.window._apply_accent(colour)
            grip = self.window._timeline._grip.name()
            self.assertGreaterEqual(self.theme.contrast(colour, grip), 4.5,
                                    f"grip on {colour} is unreadable")

    def test_every_choice_applies_without_error(self):
        for _, colour in self.theme.ACCENTS:
            self.window._apply_accent(colour)
            self.assertEqual(self.window._accent, colour)
            self.window._timeline.grab()          # must still paint

    def test_the_choice_is_remembered(self):
        from apricot import ApricotStudio
        self.window._apply_accent("#9bbf3c")
        again = ApricotStudio()
        try:
            self.assertEqual(again._accent, "#9bbf3c")
        finally:
            dispose(again)

    def test_a_corrupt_stored_choice_falls_back(self):
        from apricot import ApricotStudio
        self.window._settings.setValue("accent", "not-a-colour")
        again = ApricotStudio()
        try:
            self.assertEqual(again._accent, self.theme.DEFAULT_ACCENT)
        finally:
            dispose(again)
            self.window._settings.setValue("accent", self.theme.DEFAULT_ACCENT)

    def test_the_swatch_button_exists_and_never_takes_focus(self):
        self.assertEqual(self.window._accent_btn.objectName(), "swatch")
        self.assertEqual(self.window._accent_btn.focusPolicy(),
                         Qt.FocusPolicy.NoFocus)

    def test_the_menu_previews_every_colour(self):
        icons = [self.window._swatch(colour) for _, colour in self.theme.ACCENTS]
        self.assertEqual(len(icons), len(self.theme.ACCENTS))
        for icon in icons:
            self.assertFalse(icon.isNull(), "a colour was offered without a preview")


class TimelineOutline(unittest.TestCase):
    """The border round the timeline, which has to survive the empty state.

    An empty timeline paints its cache and returns early, so anything added to
    the loaded path alone would simply not be there when it matters most --
    before a video is open is exactly when the widget needs to say it exists.
    """

    def setUp(self):
        import theme
        from timeline import Timeline
        self.theme = theme
        self.tl = Timeline()
        self.tl.resize(400, 90)

    def corner_pixels(self, image):
        """The four edge midpoints, where a border must be and content is not."""
        w, h = image.width(), image.height()
        return [image.pixelColor(w // 2, 0).name(),
                image.pixelColor(w // 2, h - 1).name(),
                image.pixelColor(0, h // 2).name(),
                image.pixelColor(w - 1, h // 2).name()]

    def test_an_empty_timeline_is_outlined(self):
        self.tl.set_theme(self.theme.CYBERPUNK)
        edges = self.corner_pixels(self.tl.grab().toImage())
        expected = self.theme.CYBERPUNK.timeline.outline
        self.assertEqual(edges, [expected] * 4,
                         "the empty timeline has no border to show it is there")

    def test_a_loaded_timeline_keeps_the_outline(self):
        # Drawn last, so the dimming over the untrimmed ends cannot eat it.
        self.tl.set_theme(self.theme.CYBERPUNK)
        self.tl.reset(30.0)
        self.tl.set_in(10.0)
        self.tl.set_out(20.0)
        edges = self.corner_pixels(self.tl.grab().toImage())
        expected = self.theme.CYBERPUNK.timeline.outline
        self.assertEqual(edges, [expected] * 4)

    def test_default_is_not_given_one(self):
        # Default has to stay the widget it was before themes existed.
        self.tl.set_theme(self.theme.DEFAULT)
        self.assertIsNone(self.tl._pal.outline)
        edges = self.corner_pixels(self.tl.grab().toImage())
        self.assertNotIn(self.theme.CYBERPUNK.timeline.outline, edges)

    def test_the_outline_matches_the_theme_s_other_borders(self):
        # It is the same edge as every framed control in the window, so the
        # two are not allowed to drift apart into two different crimsons.
        t = self.theme.CYBERPUNK
        self.assertEqual(t.timeline.outline, t.border)

    def test_the_outline_follows_a_theme_switch(self):
        self.tl.set_theme(self.theme.CYBERPUNK)
        self.assertIsNotNone(self.tl._pal.outline)
        self.tl.set_theme(self.theme.DEFAULT)
        self.assertIsNone(self.tl._pal.outline)


class ChamferedChrome(unittest.TestCase):
    """The custom-painted button, which needs a QApplication but not a window.

    The claim being tested is that Default did not change. ChamferButton is
    used for every button in the app now, so "it falls through to Qt when the
    theme has no notch" has to be true in pixels, not just in intent.
    """

    def setUp(self):
        import chrome
        import theme
        self.chrome = chrome
        self.theme = theme
        self.addCleanup(chrome.set_look, theme.DEFAULT, theme.DEFAULT_ACCENT)
        self.addCleanup(chrome.set_quiet, False)

    @staticmethod
    def enter(widget):
        """Qt insists on a real QEnterEvent, positions and all."""
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QEnterEvent
        where = QPointF(widget.width() / 2, widget.height() / 2)
        widget.enterEvent(QEnterEvent(where, where, where))

    @staticmethod
    def leave(widget):
        from PyQt6.QtCore import QEvent
        widget.leaveEvent(QEvent(QEvent.Type.Leave))

    def button(self, text="Export", name="", w=90, h=28):
        b = self.chrome.ChamferButton(text)
        if name:
            b.setObjectName(name)
        b.resize(w, h)
        return b

    def test_default_renders_exactly_like_a_plain_button(self):
        from PyQt6.QtWidgets import QPushButton
        self.chrome.set_look(self.theme.DEFAULT, self.theme.DEFAULT_ACCENT)
        mine = self.button()
        plain = QPushButton("Export")
        plain.resize(90, 28)
        self.assertEqual(mine.grab().toImage(), plain.grab().toImage(),
                         "the Default theme should not go near the new painter")

    def test_a_chamfered_theme_does_not_render_like_a_plain_button(self):
        from PyQt6.QtWidgets import QPushButton
        self.chrome.set_look(self.theme.CYBERPUNK)
        mine = self.button()
        plain = QPushButton("Export")
        plain.resize(90, 28)
        self.assertNotEqual(mine.grab().toImage(), plain.grab().toImage())

    def test_the_corner_is_actually_cut(self):
        self.chrome.set_look(self.theme.CYBERPUNK)
        image = self.button(name="primary").grab().toImage()
        # The notch is 7px, so a pixel well inside it cannot be the fill that
        # the middle of the button is painted with.
        self.assertNotEqual(image.pixel(1, 1), image.pixel(45, 14))

    def test_the_label_is_upper_cased_without_changing_the_button(self):
        # Qt has no text-transform, so the painter does it -- but text() is
        # what the rest of the app and the tests read.
        self.chrome.set_look(self.theme.CYBERPUNK)
        b = self.button(text="Export")
        self.assertEqual(b.text(), "Export")

    def test_the_glitch_holds_still_while_an_export_runs(self):
        self.chrome.set_look(self.theme.CYBERPUNK)
        self.chrome.set_quiet(True)
        b = self.button()
        self.enter(b)
        self.assertFalse(b._glitching(), "a moving label over a running export")

    def test_an_export_starting_stops_a_glitch_already_running(self):
        # The case the guard in _glitching() exists for. Checking only that a
        # *new* hover is refused tests enterEvent and nothing else: the pointer
        # is usually already on the export button at the moment it is clicked.
        self.chrome.set_look(self.theme.CYBERPUNK)
        b = self.button()
        self.enter(b)
        self.assertTrue(b._glitching())
        self.chrome.set_quiet(True)
        self.assertFalse(b._glitching(), "a label still moving under an export")

    def test_the_glitch_stops_when_the_pointer_leaves(self):
        self.chrome.set_look(self.theme.CYBERPUNK)
        b = self.button()
        self.enter(b)
        self.assertTrue(b._glitching())
        self.leave(b)
        self.assertFalse(b._glitching())

    def test_a_theme_without_a_glitch_never_starts_one(self):
        self.chrome.set_look(self.theme.DEFAULT, self.theme.DEFAULT_ACCENT)
        b = self.button()
        self.enter(b)
        self.assertFalse(b._glitching())

    def test_a_disabled_button_does_not_glitch(self):
        self.chrome.set_look(self.theme.CYBERPUNK)
        b = self.button()
        b.setEnabled(False)
        self.enter(b)
        self.assertFalse(b._glitching())

    def test_it_paints_at_every_size_it_could_be_given(self):
        self.chrome.set_look(self.theme.CYBERPUNK)
        for w, h in ((90, 28), (40, 18), (8, 6), (300, 60)):
            with self.subTest(size=(w, h)):
                self.button(w=w, h=h).grab()      # must not raise


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class Themes(unittest.TestCase):
    """Switching theme has to reach every part that draws itself.

    The stylesheet is the easy half. The timeline paints with its own palette,
    the chamfered buttons paint with a module-level one, the scanline overlay
    exists or does not, and the application font is set programmatically --
    none of which Qt updates because a sheet was replaced.
    """

    def setUp(self):
        import theme
        from apricot import ApricotStudio
        self.theme = theme
        self.window = ApricotStudio()
        self.window.show()
        self.addCleanup(self._restore)

    def _restore(self):
        self.window._apply_theme(self.theme.DEFAULT, self.theme.DEFAULT_ACCENT)
        self.window._settings.setValue("theme", self.theme.DEFAULT.key)
        self.window._settings.setValue("accent", self.theme.DEFAULT_ACCENT)
        dispose(self.window)

    def test_starts_on_default(self):
        self.assertEqual(self.window._theme.key, "default")

    def test_switching_restyles_the_window(self):
        self.window._apply_theme("cyberpunk")
        sheet = self.window.styleSheet()
        self.assertIn(self.theme.CYBERPUNK.default_accent, sheet)
        self.assertIn(self.theme.CYBERPUNK.background, sheet)
        self.assertNotIn(self.theme.DEFAULT.surface, sheet)

    def test_switching_repaints_the_timeline_too(self):
        # The half that silently keeps the old colours if only the sheet moves.
        before = self.window._timeline._pal.bg.name()
        self.window._apply_theme("cyberpunk")
        after = self.window._timeline._pal.bg.name()
        self.assertNotEqual(before, after)
        self.assertEqual(after, self.theme.CYBERPUNK.timeline.bg)
        self.assertEqual(self.window._timeline._accent.name(),
                         self.theme.CYBERPUNK.default_accent)

    def test_switching_reaches_the_custom_painted_chrome(self):
        import chrome
        self.window._apply_theme("cyberpunk")
        self.assertEqual(chrome.look()[0].key, "cyberpunk")
        self.assertEqual(chrome.look()[1], self.theme.CYBERPUNK.default_accent)

    def test_the_picker_is_hidden_when_the_theme_owns_its_colour(self):
        self.assertTrue(self.window._accent_btn.isVisible())
        self.window._apply_theme("cyberpunk")
        self.assertFalse(self.window._accent_btn.isVisible())
        self.window._apply_theme("default")
        self.assertTrue(self.window._accent_btn.isVisible())

    def test_a_trip_through_cyberpunk_does_not_lose_the_accent(self):
        # A theme with its own colour paints it without overwriting the choice
        # underneath, or every visit would cost the user their accent.
        self.window._apply_accent("#3d92e0")
        self.window._apply_theme("cyberpunk")
        self.assertEqual(self.window._painted_accent,
                         self.theme.CYBERPUNK.default_accent)
        self.window._apply_theme("default")
        self.assertEqual(self.window._accent, "#3d92e0")
        self.assertIn("#3d92e0", self.window.styleSheet())

    def test_a_round_trip_restores_the_default_look_exactly(self):
        # The regression that proves adding a theme did not disturb the one
        # that was already there.
        before = self.window.styleSheet()
        timeline_before = self.window._timeline._pal.bg.name()
        self.window._apply_theme("cyberpunk")
        self.window._apply_theme("default")
        self.assertEqual(self.window.styleSheet(), before)
        self.assertEqual(self.window._timeline._pal.bg.name(), timeline_before)

    def test_the_choice_is_remembered(self):
        from apricot import ApricotStudio
        self.window._apply_theme("cyberpunk")
        again = ApricotStudio()
        try:
            self.assertEqual(again._theme.key, "cyberpunk")
        finally:
            dispose(again)

    def test_a_corrupt_stored_theme_falls_back(self):
        from apricot import ApricotStudio
        self.window._settings.setValue("theme", "not-a-theme")
        again = ApricotStudio()
        try:
            self.assertIs(again._theme, self.theme.DEFAULT)
        finally:
            dispose(again)

    def test_the_theme_button_exists_and_never_takes_focus(self):
        self.assertEqual(self.window._theme_btn.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertTrue(self.window._theme_btn.isVisible())

    def test_the_overlay_appears_only_for_a_scanline_theme(self):
        self.assertIsNone(self.window._overlay)
        self.window._apply_theme("cyberpunk")
        self.assertIsNotNone(self.window._overlay)
        self.assertTrue(self.window._overlay.isVisible())
        # It covers every control, so a click has to pass straight through it.
        self.assertTrue(self.window._overlay.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.window._apply_theme("default")
        self.assertFalse(self.window._overlay.isVisible())

    def test_every_theme_paints_without_error(self):
        for key in self.theme.THEMES:
            with self.subTest(theme=key):
                self.window._apply_theme(key)
                self.window._timeline.grab()      # must still paint
                self.window.grab()

    def test_the_bundled_face_reaches_the_application(self):
        import apricot
        apricot._FONT_FAMILIES[:] = self.theme.load_fonts()
        if "Rajdhani" not in apricot._FONT_FAMILIES:
            self.skipTest("Rajdhani is not bundled in this checkout")
        self.window._apply_theme("cyberpunk")
        self.assertEqual(QApplication.instance().font().family(), "Rajdhani")
        # And the theme without one of its own gives the desktop's font back.
        self.window._apply_theme("default")
        self.assertNotEqual(QApplication.instance().font().family(), "Rajdhani")

    def test_a_theme_without_a_face_does_not_need_the_files(self):
        # A source checkout with no fonts/ still has to render Default.
        import apricot
        kept = list(apricot._FONT_FAMILIES)
        apricot._FONT_FAMILIES.clear()
        try:
            self.window._apply_theme("cyberpunk")
            self.window.grab()
        finally:
            apricot._FONT_FAMILIES[:] = kept


class TrashFallback(unittest.TestCase):
    """The wastebasket path, written by hand.

    Inside a flatpak, gio routes trashing through the Trash portal, which this
    desktop advertises but does not implement -- so the safe option of a
    destructive feature has to work without it. No display needed: this is
    filesystem work, not widget work.
    """

    def setUp(self):
        import tempfile
        from apricot import ApricotStudio
        self.app_cls = ApricotStudio
        self.home = tempfile.mkdtemp(prefix="apricot-trash-")
        self.trash = os.path.join(self.home, ".local", "share", "Trash")
        self._real_expanduser = os.path.expanduser
        os.path.expanduser = lambda p: (
            p.replace("~", self.home, 1) if p.startswith("~") else self._real_expanduser(p))
        self.addCleanup(self._restore)

    def _restore(self):
        import shutil
        os.path.expanduser = self._real_expanduser
        shutil.rmtree(self.home, ignore_errors=True)

    def make_file(self, name="clip.mp4", size=4096):
        path = os.path.join(self.home, name)
        with open(path, "wb") as handle:
            handle.write(b"\7" * size)
        return path

    def read_info(self, name):
        with open(os.path.join(self.trash, "info", f"{name}.trashinfo")) as handle:
            return handle.read()

    def test_the_file_moves_into_the_wastebasket(self):
        path = self.make_file()
        ok, err = self.app_cls._trash_by_hand(path)
        self.assertTrue(ok, err)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.exists(os.path.join(self.trash, "files", "clip.mp4")))

    def test_it_records_where_the_file_came_from(self):
        # Without this the file manager cannot offer to restore it.
        path = self.make_file()
        self.app_cls._trash_by_hand(path)
        info = self.read_info("clip.mp4")
        self.assertIn("[Trash Info]", info)
        self.assertIn(f"Path={path}", info)
        self.assertRegex(info, r"DeletionDate=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_paths_needing_escaping_are_encoded(self):
        path = self.make_file("holiday clip #2.mp4")
        ok, _ = self.app_cls._trash_by_hand(path)
        self.assertTrue(ok)
        info = self.read_info("holiday clip #2.mp4")
        self.assertIn("%20", info)
        self.assertNotIn(" ", info.split("Path=")[1].split("\n")[0])

    def test_contents_survive_intact(self):
        path = self.make_file(size=200_000)
        original = open(path, "rb").read()
        self.app_cls._trash_by_hand(path)
        with open(os.path.join(self.trash, "files", "clip.mp4"), "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(self):
        first = self.make_file()
        self.app_cls._trash_by_hand(first)
        second = self.make_file()
        ok, err = self.app_cls._trash_by_hand(second)
        self.assertTrue(ok, err)
        trashed = sorted(os.listdir(os.path.join(self.trash, "files")))
        self.assertEqual(len(trashed), 2, f"one overwrote the other: {trashed}")

    def test_every_file_keeps_its_own_record(self):
        for _ in range(3):
            self.app_cls._trash_by_hand(self.make_file())
        files = os.listdir(os.path.join(self.trash, "files"))
        infos = os.listdir(os.path.join(self.trash, "info"))
        self.assertEqual(len(files), len(infos))

    def test_a_missing_file_fails_without_leaving_a_record(self):
        ok, err = self.app_cls._trash_by_hand(os.path.join(self.home, "nope.mp4"))
        self.assertFalse(ok)
        self.assertTrue(err)
        # A record with no file behind it shows as a broken entry in Dolphin.
        self.assertFalse(os.listdir(os.path.join(self.trash, "info")))

    def cross_device_rename(self):
        """Make os.rename behave as it does across two bind mounts."""
        import unittest.mock
        return unittest.mock.patch(
            "os.rename", side_effect=OSError(errno.EXDEV, "Invalid cross-device link"))

    def test_it_still_works_when_rename_cannot_cross_mounts(self):
        # The real sandbox case: flatpak bind-mounts each granted path
        # separately, so rename() refuses even though one filesystem holds both.
        # A same-directory test can never reach this branch, hence the patch.
        path = self.make_file(size=50_000)
        original = open(path, "rb").read()
        with self.cross_device_rename():
            ok, err = self.app_cls._trash_by_hand(path)
        self.assertTrue(ok, f"cross-device trashing failed: {err}")
        self.assertFalse(os.path.exists(path), "the original was left behind")
        with open(os.path.join(self.trash, "files", "clip.mp4"), "rb") as handle:
            self.assertEqual(handle.read(), original, "the copy is not intact")

    def test_a_failed_cross_device_copy_leaves_nothing_behind(self):
        import unittest.mock
        path = self.make_file()
        with self.cross_device_rename(), \
             unittest.mock.patch("shutil.copy2", side_effect=OSError("disk full")):
            ok, err = self.app_cls._trash_by_hand(path)
        self.assertFalse(ok)
        self.assertTrue(os.path.exists(path), "the original must survive a failure")
        self.assertFalse(os.listdir(os.path.join(self.trash, "info")),
                         "left an orphaned record")
        self.assertFalse(os.listdir(os.path.join(self.trash, "files")),
                         "left a partial copy")

    def test_the_wastebasket_is_created_if_absent(self):
        self.assertFalse(os.path.exists(self.trash))
        self.app_cls._trash_by_hand(self.make_file())
        self.assertTrue(os.path.isdir(os.path.join(self.trash, "files")))


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class CloseFile(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from apricot import ApricotStudio
        from tests import fixtures
        self.tmp = tempfile.mkdtemp(prefix="apricot-close-")
        self.source = os.path.join(self.tmp, "clip.mp4")
        shutil.copy(fixtures.sample("allp"), self.source)
        self.window = ApricotStudio()
        self.window.show()
        self.window.load(self.source)

    def tearDown(self):
        import shutil
        dispose(self.window)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_closing_returns_the_window_to_empty(self):
        self.assertIsNotNone(self.window._info)
        self.window.close_file()
        self.assertIsNone(self.window._info)
        self.assertEqual(self.window._title.text(), "No file loaded")
        self.assertEqual(self.window._badge.text(), "")
        self.assertEqual(self.window._out_name.text(), "")
        self.assertEqual(self.window._out_dir.text(), "")

    def test_closing_disables_everything_again(self):
        self.window.close_file()
        for widget in (self.window._export_btn, self.window._play_btn,
                       self.window._fmt_box, self.window._delete_source,
                       self.window._close_btn):
            self.assertFalse(widget.isEnabled())

    def test_closing_leaves_the_file_on_disk(self):
        self.window.close_file()
        self.assertTrue(os.path.exists(self.source), "Close must not delete anything")

    def test_closing_clears_the_timeline(self):
        self.window._timeline.set_in(1.0)
        self.window._timeline.set_out(3.0)
        self.window.close_file()
        self.assertEqual(self.window._timeline._duration, 0.0)
        self.assertEqual(self.window._timeline._thumbs, [])

    def test_closing_with_nothing_open_is_harmless(self):
        self.window.close_file()
        self.window.close_file()
        self.assertIsNone(self.window._info)

    def test_a_file_can_be_opened_again_after_closing(self):
        self.window.close_file()
        self.window.load(self.source)
        self.assertIsNotNone(self.window._info)
        self.assertTrue(self.window._export_btn.isEnabled())


class WhatTheDeletePromptSays(unittest.TestCase):
    """Regression: the prompt read its duration off whatever file was open.

    Every other number in it comes from the record captured when the export
    started, which is the whole reason that record exists -- another video can
    be opened while an encode runs. The duration did not, so the dialog would
    name one file and describe the length of another.
    """

    def setUp(self):
        from apricot import Exported, delete_prompt
        self.delete_prompt = delete_prompt
        self.record = Exported(source="/videos/replay.mp4", output="/videos/clip.mp4",
                               duration=600.0, kept=60.0)

    def test_it_names_the_file_that_was_cut(self):
        text, _ = self.delete_prompt(self.record, 110_000_000)
        self.assertIn("replay.mp4", text)

    def test_the_length_is_the_one_that_was_recorded(self):
        _, detail = self.delete_prompt(self.record, 110_000_000)
        self.assertIn("00:10:00.000", detail,
                      "the length must come from the record, not the open file")

    def test_it_says_how_much_is_being_thrown_away(self):
        _, detail = self.delete_prompt(self.record, 110_000_000)
        self.assertIn("60.0s", detail)
        self.assertIn("10%", detail)     # 60s kept out of 600
        self.assertIn("90%", detail)     # and the rest, gone with the file

    def test_it_reassures_about_the_clip(self):
        _, detail = self.delete_prompt(self.record, 110_000_000)
        self.assertIn("clip.mp4", detail)

    def test_a_zero_length_record_does_not_divide_by_zero(self):
        from apricot import Exported
        blank = Exported(source="/a.mp4", output="/b.mp4", duration=0.0, kept=0.0)
        self.delete_prompt(blank, 0)


class ShowingTheClipInAFileManager(unittest.TestCase):
    """The URI used to be interpolated, so a space or a # broke it."""

    def uri(self, path):
        from apricot import ApricotStudio
        args = ApricotStudio._reveal_args(path)
        return next(a.removeprefix("array:string:") for a in args
                    if a.startswith("array:string:"))

    def test_an_ordinary_path_is_a_file_url(self):
        self.assertEqual(self.uri("/videos/clip.mp4"), "file:///videos/clip.mp4")

    def test_spaces_are_encoded(self):
        self.assertNotIn(" ", self.uri("/videos/my clip.mp4"))

    def test_a_hash_cannot_cut_the_path_short(self):
        uri = self.uri("/videos/my clip #2.mp4")
        self.assertNotIn("#", uri)
        self.assertTrue(uri.endswith(".mp4"), uri)

    def test_percent_signs_survive(self):
        self.assertIn("%25", self.uri("/videos/100%.mp4"))

    def test_non_ascii_names_are_encoded(self):
        uri = self.uri("/videos/rêve.mp4")
        self.assertEqual(uri, "file:///videos/r%C3%AAve.mp4")

    def test_the_uri_is_plain_ascii(self):
        # Whatever is on the other end of the bus is parsing this, not reading it.
        uri = self.uri("/videos/rêve #1 (best).mp4")
        self.assertTrue(uri.isascii(), uri)

    def test_the_call_still_asks_the_file_manager_to_select_it(self):
        from apricot import ApricotStudio
        args = ApricotStudio._reveal_args("/videos/clip.mp4")
        self.assertIn("org.freedesktop.FileManager1.ShowItems", args)


class TheOpenDialogOffersWhatCanBeDropped(unittest.TestCase):
    """Two lists of extensions, kept by hand, had drifted apart."""

    def test_every_droppable_extension_appears_in_the_filter(self):
        import apricot
        for ext in apricot.VIDEO_EXTS:
            with self.subTest(ext=ext):
                self.assertIn(f"*{ext}", apricot.VIDEO_SUFFIXES)

    def test_the_filter_is_only_extensions_that_can_be_dropped(self):
        import apricot
        offered = {p.removeprefix("*") for p in apricot.VIDEO_SUFFIXES.split()}
        self.assertEqual(offered, apricot.VIDEO_EXTS)


class BrokenExportsAreClearedAway(unittest.TestCase):
    """A failed encode leaves a file that plays for a second and stops.

    It must go -- but only if this export is what put it there. ffmpeg opens the
    destination with -y, so an overwrite is lost the moment the encode starts;
    if it failed before reaching that point, the file sitting there is whole and
    belongs to the user.
    """

    def setUp(self):
        import tempfile
        import export
        self.export = export
        self.tmp = tempfile.mkdtemp(prefix="apricot-broken-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def half_written(self, name="clip.mp4"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(b"\0" * 512)
        return path

    def finish(self, path, *, created, code=1, cancelled=False):
        exporter = self.export.Exporter()
        exporter._plan = self.export.Plan(args=[], output=path, duration=1.0, label="x")
        exporter._created_output = created
        exporter._cancelled = cancelled
        exporter._on_finished(code, None)
        return os.path.exists(path)

    def test_a_failed_encode_takes_its_own_leftovers_with_it(self):
        self.assertFalse(self.finish(self.half_written(), created=True))

    def test_a_cancelled_encode_does_too(self):
        self.assertFalse(self.finish(self.half_written(), created=True, cancelled=True))

    def test_a_file_that_was_already_there_is_left_alone(self):
        self.assertTrue(self.finish(self.half_written(), created=False))

    def test_a_cancel_does_not_delete_a_file_it_never_opened(self):
        self.assertTrue(self.finish(self.half_written(), created=False, cancelled=True))

    def test_a_successful_encode_keeps_its_output(self):
        self.assertTrue(self.finish(self.half_written(), created=True, code=0))

    def test_start_notices_a_destination_that_already_exists(self):
        import time
        path = self.half_written("existing.mp4")
        exporter = self.export.Exporter()
        # -version exits at once, so this exercises start()'s bookkeeping
        # without an encode.
        exporter.start(self.export.Plan(args=["-version"], output=path,
                                        duration=1.0, label="x"))
        self.assertFalse(exporter._created_output)
        deadline = time.monotonic() + 10
        while exporter.running and time.monotonic() < deadline:
            _app.processEvents()
        self.assertTrue(os.path.exists(path), "it was not ours to remove")

    def test_start_notices_a_destination_it_will_create(self):
        import time
        exporter = self.export.Exporter()
        exporter.start(self.export.Plan(args=["-version"],
                                        output=os.path.join(self.tmp, "new.mp4"),
                                        duration=1.0, label="x"))
        self.assertTrue(exporter._created_output)
        deadline = time.monotonic() + 10
        while exporter.running and time.monotonic() < deadline:
            _app.processEvents()


class TheApplicationIconRenders(unittest.TestCase):
    """Whatever the drawing says, Qt has to make a picture out of it."""

    def setUp(self):
        from PyQt6.QtGui import QImageReader
        if b"svg" not in QImageReader.supportedImageFormats():
            self.skipTest("no Qt SVG image plugin here")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = os.path.join(root, "io.github.raulblideran.ApricotStudio.svg")
        if not os.path.exists(self.path):
            self.skipTest("the icon is not installed beside the source")

    def sample(self, size):
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QIcon
        pixmap = QIcon(self.path).pixmap(QSize(size, size))
        self.assertFalse(pixmap.isNull(), f"nothing came back at {size}px")
        image = pixmap.toImage()
        step = max(size // 10, 1)
        return {image.pixel(x, y)
                for x in range(0, size, step) for y in range(0, size, step)}

    def test_it_draws_at_every_size_it_will_be_asked_for(self):
        for size in (16, 24, 32, 48, 64, 128, 256):
            with self.subTest(size=size):
                self.assertGreater(len(self.sample(size)), 3,
                                   "a flat square means the drawing did not render")

    def test_it_is_not_a_blank_tile_at_taskbar_size(self):
        # 16px is where an icon that leans on thin strokes falls apart.
        self.assertGreater(len(self.sample(16)), 5)


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class EmptyWindow(unittest.TestCase):
    """A freshly opened window, before any file is chosen."""

    @classmethod
    def setUpClass(cls):
        from apricot import ApricotStudio
        cls.window = ApricotStudio()

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


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class FormatRulesSurviveAReload(unittest.TestCase):
    """Regression: loading a file handed back controls the format had disabled.

    Both _set_loaded and _rebuild_audio_box enable things on the strength of the
    file alone; neither knows GIF has no use for a quality preset or an audio
    track. Loading a second file with GIF selected left both boxes live and
    doing nothing.
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from apricot import ApricotStudio
        from tests import fixtures
        cls.tmp = tempfile.mkdtemp(prefix="apricot-reload-")
        cls.first = os.path.join(cls.tmp, "one.mp4")
        cls.second = os.path.join(cls.tmp, "two.mp4")
        shutil.copy(fixtures.sample("allp"), cls.first)
        shutil.copy(fixtures.sample("allp"), cls.second)
        cls.window = ApricotStudio()
        cls.window.show()

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.window._close_source()
        dispose(cls.window)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def gif(self):
        import export
        box = self.window._fmt_box
        box.setCurrentIndex(box.findData(export.GIF))

    def setUp(self):
        self.window.load(self.first)
        self.window._fmt_box.setCurrentIndex(0)

    def test_gif_disables_the_quality_box(self):
        self.gif()
        self.assertFalse(self.window._size_box.isEnabled())

    def test_it_is_still_disabled_after_loading_another_file(self):
        self.gif()
        self.window.load(self.second)
        self.assertFalse(self.window._size_box.isEnabled(),
                         "a control that does nothing must not look live")

    def test_audio_stays_disabled_after_loading_another_file(self):
        self.gif()
        self.window.load(self.second)
        self.assertFalse(self.window._audio_box.isEnabled())

    def test_going_back_to_source_format_hands_them_back(self):
        self.gif()
        self.window.load(self.second)
        self.window._fmt_box.setCurrentIndex(0)
        self.assertTrue(self.window._size_box.isEnabled())
        self.assertTrue(self.window._audio_box.isEnabled())

    def test_the_extension_follows_the_format_across_a_load(self):
        self.gif()
        self.window.load(self.second)
        self.assertEqual(self.window._out_ext.text(), ".gif")


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class TheWindowComesBackTheSizeItWasLeft(unittest.TestCase):
    """Geometry is remembered. The destructive option deliberately is not."""

    def setUp(self):
        from PyQt6.QtCore import QSettings
        self.settings = QSettings("ApricotStudio", "ApricotStudio")
        self.saved = self.settings.value("geometry")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.saved is None:
            self.settings.remove("geometry")
        else:
            self.settings.setValue("geometry", self.saved)

    def test_the_size_survives_a_restart(self):
        from apricot import ApricotStudio
        first = ApricotStudio()
        first.show()
        first.resize(1000, 700)
        _app.processEvents()
        first.close()          # closeEvent is what writes it down
        dispose(first)

        second = ApricotStudio()
        self.addCleanup(dispose, second)
        self.assertEqual(second.size().width(), 1000)
        self.assertEqual(second.size().height(), 700)

    def test_the_delete_option_is_never_remembered(self):
        from apricot import ApricotStudio
        first = ApricotStudio()
        first.show()
        first._delete_source.setChecked(True)
        first.close()
        dispose(first)

        second = ApricotStudio()
        self.addCleanup(dispose, second)
        self.assertFalse(second._delete_source.isChecked(),
                         "arming a destructive option must not persist")

    def test_a_stored_value_of_the_wrong_shape_is_ignored(self):
        from apricot import ApricotStudio
        self.settings.setValue("geometry", "not a geometry")
        window = ApricotStudio()
        self.addCleanup(dispose, window)
        self.assertGreater(window.size().width(), 0)


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class OpeningWhatTheSandboxHides(unittest.TestCase):
    """A file the flatpak was never allowed to see.

    ffprobe reports it in exactly the words it uses for a deleted file, so the
    window has to tell the two apart itself and offer the way out. The dialog is
    modal, so exec() is replaced throughout: either it does nothing, or it
    presses one button, which is as close to a user as this can get without a
    hand on the mouse.
    """

    def setUp(self):
        import shutil
        import tempfile
        from apricot import ApricotStudio
        self.tmp = tempfile.mkdtemp(prefix="apricot-blocked-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Not mounted in here, so nothing along it exists -- what an ungranted
        # folder looks like from inside the sandbox.
        self.folder = os.path.join(self.tmp, "PersonalFiles", "Videos")
        self.blocked = os.path.join(self.folder, "MOE_2489_1.mp4")
        self.window = ApricotStudio()
        self.window.show()
        self.addCleanup(dispose, self.window)

    def confined(self):
        """Pretend this process is the flatpak, whatever it really is."""
        import sandbox
        return unittest.mock.patch.object(sandbox, "is_sandboxed", return_value=True)

    def silent_dialog(self):
        return unittest.mock.patch.object(QMessageBox, "exec", return_value=0)

    def press(self, label):
        """Replace exec() with a press of the button carrying `label`.

        The dialog is parented to the window, so it can be found there while
        exec() stands in for the user.
        """
        def clicked(*_):
            boxes = self.window.findChildren(QMessageBox)
            self.assertTrue(boxes, "no dialog was put up")
            for button in boxes[-1].buttons():
                if button.text().replace("&", "") == label:
                    button.click()
                    return 0
            raise AssertionError(f"no “{label}” button in the dialog")

        return unittest.mock.patch.object(QMessageBox, "exec", side_effect=clicked)

    def test_a_failed_open_is_reported_and_changes_nothing_else(self):
        with unittest.mock.patch.object(
                type(self.window), "_report_open_failure") as report:
            self.window.load(self.blocked)
        report.assert_called_once()
        self.assertEqual(report.call_args.args[0], self.blocked)
        self.assertIsNone(self.window._info, "nothing was opened")
        self.assertNotIn(self.blocked, self.window._settings.value("recent", [], type=list) or [],
                         "a file that would not open must not join the recent list")

    def test_a_hidden_folder_gets_the_dialog_that_can_fix_it(self):
        with self.confined(), self.silent_dialog() as box, \
                unittest.mock.patch.object(QMessageBox, "warning") as plain:
            self.window._report_open_failure(self.blocked, "No such file or directory")
        box.assert_called_once()
        plain.assert_not_called()

    def test_an_ordinary_failure_keeps_the_plain_message(self):
        # The file is right there; ffprobe simply does not want it. Nothing a
        # permission would change, so the old wording stands.
        real = os.path.join(self.tmp, "not-really-video.mp4")
        with open(real, "wb") as handle:
            handle.write(b"junk")
        with self.confined(), self.silent_dialog() as box, \
                unittest.mock.patch.object(QMessageBox, "warning") as plain:
            self.window._report_open_failure(real, "Invalid data found when processing input")
        plain.assert_called_once()
        box.assert_not_called()
        self.assertIn("Invalid data found", plain.call_args.args[2])

    def test_a_genuinely_missing_file_keeps_the_plain_message(self):
        gone = os.path.join(self.tmp, "deleted.mp4")
        with self.confined(), self.silent_dialog() as box, \
                unittest.mock.patch.object(QMessageBox, "warning") as plain:
            self.window._report_open_failure(gone, "No such file or directory")
        plain.assert_called_once()
        box.assert_not_called()

    def test_copying_the_command_puts_a_working_line_on_the_clipboard(self):
        import sandbox
        # QMessageBox.warning builds and runs its own box down in C++, where a
        # patched exec() cannot reach it. Stubbing it keeps a regression here a
        # failure rather than a real modal dialog nobody is around to dismiss.
        with self.confined(), self.press("Copy command"), \
                unittest.mock.patch.object(QMessageBox, "warning") as plain:
            self.window._report_open_failure(self.blocked, "No such file or directory")
        plain.assert_not_called()
        copied = _app.clipboard().text()
        self.assertEqual(copied, sandbox.override_command(self.folder))
        self.assertIn(self.folder, copied)
        self.assertIn("restart", self.window._status.text().lower(),
                      "an override does nothing until the app is started again")

    def test_locating_the_file_opens_the_chooser_at_the_blocked_folder(self):
        # The chooser is the portal's, which is not confined, so it can reach
        # the folder this app cannot -- but only if it starts there.
        with self.confined(), self.press("Locate the file…"), \
                unittest.mock.patch.object(QMessageBox, "warning") as plain, \
                unittest.mock.patch.object(type(self.window), "open_file") as chooser:
            self.window._report_open_failure(self.blocked, "No such file or directory")
        plain.assert_not_called()
        chooser.assert_called_once_with(start=self.folder)

    def test_closing_the_dialog_does_nothing_at_all(self):
        with self.confined(), self.press("Close"), \
                unittest.mock.patch.object(QMessageBox, "warning") as plain, \
                unittest.mock.patch.object(type(self.window), "open_file") as chooser:
            self.window._report_open_failure(self.blocked, "No such file or directory")
        plain.assert_not_called()
        chooser.assert_not_called()


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class TheButtonsThemselves(unittest.TestCase):
    """Regression: clicking Open crashed the app, and nothing here noticed.

    QPushButton.clicked carries a checked flag, and PyQt hands a slot as many
    arguments as it will accept, so an optional positional parameter receives
    False. open_file had one, so the chooser was asked to open a bool, and an
    exception inside a slot is fatal in PyQt6 -- the window went away.

    Every test that touched open_file called it directly with a string, or
    replaced it with a mock and asserted it had been called. So the method was
    covered from every side except the one the user presses, and the defect
    lived in exactly that gap, through two releases.
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from apricot import ApricotStudio
        from tests import fixtures
        cls.tmp = tempfile.mkdtemp(prefix="apricot-buttons-")
        cls.source = os.path.join(cls.tmp, "replay.mp4")
        shutil.copy(fixtures.sample("allp"), cls.source)
        cls.window = ApricotStudio()
        cls.window.show()

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.window._close_source()
        dispose(cls.window)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def chooser(self):
        """Stand in for the Open dialog, recording the folder it was given.

        Records rather than validates, and the tests check what it collected.
        Raising here would be more faithful to the real call, which rejects a
        non-string outright -- but an exception inside a slot is fatal in PyQt6,
        so the suite would abort instead of reporting, and take every test after
        it down as well.

        The recording still has to be checked. A stand-in looser than the thing
        it replaces is precisely how this defect stayed hidden: patch
        getOpenFileName with something that accepts anything, assert only that
        it was reached, and a bool sails through the suite and crashes in front
        of a user.
        """
        from PyQt6.QtWidgets import QFileDialog
        seen = []

        def fake(parent, caption, directory, filt):
            seen.append(directory)
            return "", ""

        patch = unittest.mock.patch.object(QFileDialog, "getOpenFileName", fake)
        return patch, seen

    def test_the_open_button_survives_being_pressed(self):
        patch, seen = self.chooser()
        with patch:
            self.window._open_btn.click()
        self.assertTrue(seen, "the button never reached the chooser")

    def test_it_offers_a_folder_and_not_a_boolean(self):
        patch, seen = self.chooser()
        with patch:
            self.window._open_btn.click()
        self.assertIsInstance(seen[0], str,
                              "Qt's checked flag reached the chooser as a folder")

    def test_with_nothing_loaded_it_starts_in_videos(self):
        patch, seen = self.chooser()
        with patch:
            self.window._close_source()
            self.window._open_btn.click()
        self.assertEqual(seen[0], os.path.expanduser("~/Videos"))

    def test_with_a_file_open_it_starts_beside_it(self):
        patch, seen = self.chooser()
        with patch:
            self.window.load(self.source)
            self.window._open_btn.click()
        self.assertEqual(seen[0], self.tmp)

    def test_every_button_in_the_window_can_be_pressed(self):
        """The sweep whose absence let the one above through.

        Presses everything, with the doors out of the process held shut: the
        two file dialogs, the menus behind Recent and the accent swatch, the
        encoder, and the call that would go looking for a file manager.
        """
        import export
        from PyQt6.QtCore import QProcess
        from PyQt6.QtWidgets import QFileDialog, QMenu, QPushButton

        self.window.load(self.source)
        buttons = self.window.findChildren(QPushButton)
        self.assertGreater(len(buttons), 10, "expected the whole window's worth")

        chooser, offered = self.chooser()
        with chooser, \
                unittest.mock.patch.object(QFileDialog, "getSaveFileName",
                                           return_value=("", "")), \
                unittest.mock.patch.object(QMenu, "exec", return_value=None), \
                unittest.mock.patch.object(export.Exporter, "start"), \
                unittest.mock.patch.object(QProcess, "startDetached",
                                           return_value=True):
            for button in buttons:
                with self.subTest(button=button.text() or button.objectName()):
                    button.click()
                    _app.processEvents()

        # Not just "nothing raised": the doors are stood in for, so a control
        # handing one of them something the real call would refuse would go
        # unremarked unless what arrived is checked.
        self.assertTrue(all(isinstance(folder, str) for folder in offered),
                        f"the chooser was offered {[type(f).__name__ for f in offered]}")


@unittest.skipUnless(REAL_WINDOWS, "needs a real display")
class WhereTheClipIsSuggested(unittest.TestCase):
    """The export folder the window fills in for a freshly opened source."""

    def setUp(self):
        import shutil
        import tempfile
        from apricot import ApricotStudio
        self.tmp = tempfile.mkdtemp(prefix="apricot-suggest-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.window = ApricotStudio()
        self.addCleanup(dispose, self.window)

    def test_an_ordinary_source_exports_beside_itself(self):
        self.window._suggest_output(os.path.join(self.tmp, "clip.mp4"))
        self.assertEqual(self.window._out_dir.text(), self.tmp)
        self.assertEqual(self.window._out_name.text(), "clip_clip")

    def test_a_portal_path_exports_somewhere_a_file_can_be_written(self):
        # A file located through the portal is exposed in a one-file directory
        # that will not take a second one, so the source's folder is no answer.
        import sandbox
        doc = os.path.join(self.tmp, "doc", "a1b2c3d4")
        os.makedirs(doc)
        with unittest.mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp}):
            self.window._suggest_output(os.path.join(doc, "MOE_2489_1.mp4"))
            self.assertEqual(self.window._out_dir.text(), sandbox.fallback_output_dir())
        # A name already taken there gets a number, so only the stem is fixed.
        self.assertTrue(self.window._out_name.text().startswith("MOE_2489_1_clip"),
                        self.window._out_name.text())


if __name__ == "__main__":
    unittest.main()
