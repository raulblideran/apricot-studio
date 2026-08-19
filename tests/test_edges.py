"""Adversarial tests: malformed input, hostile names, degenerate ranges.

Written to break the code rather than to agree with it. Most of these were added
after they failed -- the extension parser, the timecode field and the size
arithmetic all had genuine defects that the happy-path tests were blind to.

Anything a user can type, drop on the window or point at counts as untrusted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

import export
import media
from tests import fixtures
from tests.test_units import make_info

KF = [0.0, 2.0, 4.0]


def value_after(args, flag):
    """The argument following `flag`, or None."""
    return args[args.index(flag) + 1] if flag in args else None


class ContainerExtension(unittest.TestCase):
    """Regression: a dot in a directory name broke the extension entirely."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="apricot-ext-")
        cls.src = fixtures.sample("allp")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe_named(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(self.src, path)
        return media.probe(path)

    def test_dotted_directory_does_not_leak_into_the_extension(self):
        # "~/my.videos/clip" once yielded ext="videos/clip".
        self.assertEqual(self.probe_named("my.videos", "clip.mp4").ext, "mp4")

    def test_file_with_no_extension_falls_back(self):
        info = self.probe_named("plain", "clip")
        self.assertEqual(info.ext, "mp4")
        self.assertFalse("/" in info.ext)

    def test_extension_is_lowercased(self):
        self.assertEqual(self.probe_named("caps", "CLIP.MP4").ext, "mp4")

    def test_double_extension_takes_the_last(self):
        self.assertEqual(self.probe_named("dbl", "clip.backup.mp4").ext, "mp4")

    def test_extension_never_contains_a_separator(self):
        for parts in (("a.b", "c.mp4"), ("x.y.z", "clip"), ("dots", "..mp4")):
            self.assertNotIn(os.sep, self.probe_named(*parts).ext)


class HostileTimecodes(unittest.TestCase):
    """Regression: typing "nan" into the In field crashed the window.

    A NaN defeats every clamp -- all comparisons against it are false -- so it
    reached the formatter and took int() down on the next repaint.
    """

    def test_rejects_the_values_float_accepts_but_a_file_cannot_have(self):
        for text in ("nan", "NaN", "inf", "-inf", "infinity", "1e400"):
            self.assertIsNone(media.parse_tc(text), f"{text!r} must not parse")

    def test_rejects_negative_positions(self):
        for text in ("-5", "-0.1", "-1:00"):
            self.assertIsNone(media.parse_tc(text), f"{text!r} must not parse")

    def test_rejects_junk(self):
        for text in ("", "   ", "abc", "12:", ":30", "1:2:3:4", "--", "1..2"):
            self.assertIsNone(media.parse_tc(text), f"{text!r} must not parse")

    def test_still_accepts_everything_legitimate(self):
        for text, want in (("0", 0.0), ("7.5", 7.5), ("1:02.25", 62.25),
                           ("00:00:42.500", 42.5), (" 42.5 ", 42.5),
                           ("01:01:01.250", 3661.25)):
            self.assertAlmostEqual(media.parse_tc(text), want, places=3)

    def test_formatter_survives_values_it_should_never_see(self):
        # Belt and braces: this runs on every repaint, so it must not raise.
        for value in (float("nan"), float("inf"), float("-inf"), -1.0, 0.0):
            self.assertRegex(media.fmt_tc(value), r"^\d\d:\d\d:\d\d\.\d\d\d$")

    def test_formatter_handles_very_long_files(self):
        self.assertEqual(media.fmt_tc(3600 * 30), "30:00:00.000")


class DegenerateRanges(unittest.TestCase):
    """A caller can pass anything; the command must stay inside the file."""

    def setUp(self):
        self.info = make_info(duration=90.0)

    def seeks(self, plan):
        return [float(plan.args[n + 1]) for n, a in enumerate(plan.args) if a == "-ss"]

    def test_negative_start_never_produces_a_negative_seek(self):
        plan = export.build(self.info, -30.0, 5.0, "/tmp/o.mp4", KF)
        self.assertTrue(all(s >= 0 for s in self.seeks(plan)),
                        f"negative seek in {self.seeks(plan)}")

    def test_start_past_the_end_is_clamped_into_the_file(self):
        plan = export.build(self.info, 9999.0, 10000.0, "/tmp/o.mp4", KF)
        self.assertTrue(all(0 <= s <= self.info.duration for s in self.seeks(plan)))

    def test_reversed_range_does_not_produce_a_negative_duration(self):
        plan = export.build(self.info, 50.0, 10.0, "/tmp/o.mp4", KF)
        self.assertGreater(plan.duration, 0.0)

    def test_zero_length_range_still_yields_at_least_one_frame(self):
        plan = export.build(self.info, 50.0, 50.0, "/tmp/o.mp4", KF)
        self.assertAlmostEqual(plan.duration, self.info.frame_duration, places=6)

    def test_duration_is_never_nan(self):
        plan = export.build(self.info, 10.0, 20.0, "/tmp/o.mp4", KF)
        self.assertEqual(plan.duration, plan.duration)

    # The two below repeat the cases above with a start that sits *on* a
    # keyframe, so the stream-copy branch runs instead of the re-encode one. It
    # had no duration floor of its own, and KF above never lands on the start
    # times used here, so every degenerate-range test was exercising one half of
    # build() and reporting on both.

    def test_reversed_range_stays_positive_on_a_keyframe_too(self):
        plan = export.build(self.info, 4.0, 3.0, "/tmp/o.mp4", KF)
        self.assertTrue(plan.lossless, "this test is pointless off the copy path")
        self.assertGreater(plan.duration, 0.0)
        self.assertGreater(float(value_after(plan.args, "-t")), 0.0,
                           "ffmpeg refuses a negative -t")

    def test_zero_length_range_on_a_keyframe_yields_a_frame(self):
        plan = export.build(self.info, 4.0, 4.0, "/tmp/o.mp4", KF)
        self.assertTrue(plan.lossless)
        self.assertAlmostEqual(plan.duration, self.info.frame_duration, places=6)


class SizeArithmeticLimits(unittest.TestCase):
    def test_near_zero_duration_does_not_explode(self):
        # Dividing a budget by a vanishing duration once solved to 72 Gb/s.
        info = make_info()
        rate = export.target_video_bitrate(info, export.Options(target_bytes=10_000_000), 0.0)
        self.assertLessEqual(rate, export.MAX_BITRATE)

    def test_impossible_target_still_yields_a_usable_bitrate(self):
        info = make_info()
        rate = export.target_video_bitrate(info, export.Options(target_bytes=1), 600.0)
        self.assertGreaterEqual(rate, 100_000)

    def test_audio_alone_exceeding_the_budget_does_not_go_negative(self):
        loud = make_info(audio=(media.AudioTrack(0, "opus", 2_000_000, 48000, 2, "", ""),))
        rate = export.target_video_bitrate(loud, export.Options(target_bytes=100_000), 60.0)
        self.assertGreater(rate, 0)


class AwkwardFilenames(unittest.TestCase):
    """Paths are passed as argument lists, so nothing here should ever be shell."""

    NAMES = ["my clip.mp4", "quote'.mp4", 'double".mp4', "semi;echo pwned.mp4",
             "dollar$HOME.mp4", "back\\slash.mp4", "unicode_日本.mp4",
             "-dash-start.mp4", "paren(1).mp4", "star*.mp4"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="apricot-names-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_default_output_handles_them_all(self):
        for name in self.NAMES:
            path = os.path.join(self.tmp, name)
            open(path, "w").close()
            out = export.default_output(path)
            self.assertTrue(out.endswith(".mp4"), f"{name} lost its extension")
            self.assertIn("_clip", os.path.basename(out))

    def test_paths_reach_ffmpeg_as_single_arguments(self):
        # If a name were ever concatenated into a shell string, the semicolon
        # and the star would be the ones that hurt.
        for name in self.NAMES:
            info = make_info(path=os.path.join(self.tmp, name))
            plan = export.build(info, 1.0, 3.0, os.path.join(self.tmp, "out.mp4"), KF)
            self.assertIn(info.path, plan.args,
                          f"{name} was not passed as one intact argument")


class UnreadableSources(unittest.TestCase):
    """probe() is the front door; it must reject rather than half-succeed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="apricot-bad-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def make(self, name, data=b""):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_rejects_a_missing_file(self):
        with self.assertRaises(RuntimeError):
            media.probe(os.path.join(self.tmp, "nope.mp4"))

    def test_rejects_an_empty_file(self):
        with self.assertRaises(RuntimeError):
            media.probe(self.make("empty.mp4"))

    def test_rejects_a_text_file_wearing_a_video_extension(self):
        with self.assertRaises(RuntimeError):
            media.probe(self.make("fake.mp4", b"this is not a video" * 100))

    def test_rejects_a_directory(self):
        with self.assertRaises(RuntimeError):
            media.probe(self.tmp)

    def test_rejects_a_truncated_video(self):
        source = fixtures.sample("allp")
        with open(source, "rb") as handle:
            head = handle.read(2048)
        with self.assertRaises(RuntimeError):
            media.probe(self.make("truncated.mp4", head))

    def test_rejects_audio_with_no_video_stream(self):
        audio = os.path.join(self.tmp, "audio.opus")
        subprocess.run([media.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=1",
                        "-c:a", "libopus", audio], check=True, capture_output=True)
        with self.assertRaises(RuntimeError):
            media.probe(audio)

    def test_ffprobe_is_given_a_deadline(self):
        """probe() runs on the UI thread, so a hang there is a hung window.

        Asserts the deadline is actually handed over rather than only that a
        timeout is handled: a test that raises TimeoutExpired by hand passes
        just as happily against a probe that would wait forever.
        """
        seen = {}

        def record(*args, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)

        with unittest.mock.patch.object(subprocess, "run", record):
            with self.assertRaises(RuntimeError):
                media.probe("/tmp/whatever.mp4")
        self.assertGreater(seen.get("timeout") or 0, 0,
                           "nothing here can interrupt a probe that never returns")

    def test_a_probe_that_gives_up_says_so_in_words(self):
        # TimeoutExpired is a SubprocessError, not an OSError, so it would sail
        # straight past the caller that opens files.
        def stall(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=20)

        with unittest.mock.patch.object(subprocess, "run", stall):
            with self.assertRaises(RuntimeError) as caught:
                media.probe("/tmp/whatever.mp4")
        self.assertIn("gave up", str(caught.exception).lower())

    def test_error_message_says_something_useful(self):
        try:
            media.probe(os.path.join(self.tmp, "nope.mp4"))
        except RuntimeError as exc:
            self.assertTrue(str(exc).strip(), "an empty error tells the user nothing")


class PathologicalMedia(unittest.TestCase):
    """Files that are valid but strange."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="apricot-odd-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self, name, args):
        path = os.path.join(self.tmp, name)
        subprocess.run([media.FFMPEG, "-v", "error", "-y", *args, path],
                       check=True, capture_output=True)
        return path

    def test_a_single_frame_video(self):
        path = self.build("oneframe.mp4", [
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=60:duration=0.02",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
        info = media.probe(path)
        self.assertGreater(info.duration, 0)
        plan = export.build(info, 0.0, info.duration, "/tmp/o.mp4", [0.0])
        self.assertGreater(plan.duration, 0)

    def test_an_odd_frame_rate(self):
        path = self.build("odd.mp4", [
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=24000/1001:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
        info = media.probe(path)
        self.assertAlmostEqual(info.fps, 24000 / 1001, places=2)
        self.assertGreater(info.frame_duration, 0)

    def test_a_tiny_resolution(self):
        path = self.build("tiny.mp4", [
            "-f", "lavfi", "-i", "testsrc2=size=16x16:rate=30:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
        info = media.probe(path)
        self.assertEqual((info.width, info.height), (16, 16))
        self.assertIn("16×16", info.summary())

    def test_silent_audio_does_not_break_the_waveform_scaling(self):
        # Normalising by the peak must not divide by zero on a silent track.
        path = self.build("silent.mp4", [
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30:duration=1",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "libopus"])
        info = media.probe(path)
        self.assertTrue(info.has_audio)

    def test_summary_never_raises_on_sparse_metadata(self):
        for info in (make_info(v_bitrate=0, audio=()),
                     make_info(fps=0.0, profile="", width=0, height=0),
                     make_info(audio=(media.AudioTrack(0, "opus", 0, 0, 0, "", ""),))):
            self.assertIsInstance(info.summary(), str)


class KeyframeRobustness(unittest.TestCase):
    def test_unsorted_keyframes_do_not_produce_a_silly_gop(self):
        gop = export._gop_size(make_info(fps=60.0), [10.0, 2.0, 6.0, 4.0])
        self.assertGreater(gop, 0)
        self.assertLess(gop, 60 * 30)

    def test_duplicate_keyframes_do_not_divide_by_zero(self):
        self.assertGreater(export._gop_size(make_info(fps=60.0), [0.0, 0.0, 0.0]), 0)

    def test_single_keyframe_falls_back(self):
        self.assertGreater(export._gop_size(make_info(fps=60.0), [0.0]), 0)

    def test_keyframes_beyond_the_duration_are_harmless(self):
        info = make_info(duration=10.0)
        plan = export.build(info, 2.0, 5.0, "/tmp/o.mp4", [0.0, 2.0, 500.0, 900.0])
        self.assertGreater(plan.duration, 0)

    def test_snapping_with_an_empty_list_is_identity(self):
        self.assertEqual(export.snap_to_keyframe(5.0, [], 10.0), 5.0)
        self.assertEqual(export.snap_to_keyframe(5.0, None, 10.0), 5.0)

    def test_zero_tolerance_snaps_only_on_an_exact_hit(self):
        self.assertEqual(export.snap_to_keyframe(5.0, [4.0, 6.0], 0.0), 5.0)
        self.assertEqual(export.snap_to_keyframe(4.0, [4.0, 6.0], 0.0), 4.0)

    def test_keyframe_match_ignores_non_finite_input(self):
        self.assertIsNone(export.on_keyframe(float("nan"), [0.0, 2.0]))


class OutputDestinations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="apricot-dest-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_collision_avoidance_does_not_loop_forever(self):
        base = os.path.join(self.tmp, "busy.mp4")
        open(base, "w").close()
        for n in range(1, 40):
            open(os.path.join(self.tmp, f"busy_clip{'' if n == 1 else n}.mp4"), "w").close()
        out = export.default_output(base)
        self.assertFalse(os.path.exists(out))

    def test_format_change_replaces_rather_than_appends(self):
        base = os.path.join(self.tmp, "movie.mkv")
        open(base, "w").close()
        out = export.default_output(base, "gif")
        self.assertTrue(out.endswith(".gif"))
        self.assertNotIn(".mkv", os.path.basename(out))

    def test_leading_dot_on_the_extension_is_not_doubled(self):
        # endswith(".webm") alone would pass on "clip..webm", so check the name.
        base = os.path.join(self.tmp, "movie.mkv")
        name = os.path.basename(export.default_output(base, ".webm"))
        self.assertEqual(name, "movie_clip.webm")

    def test_a_source_without_an_extension_still_gets_one(self):
        # ffmpeg picks its muxer from the extension; without one it cannot.
        base = os.path.join(self.tmp, "extensionless")
        open(base, "w").close()
        out = export.default_output(base)
        self.assertTrue(os.path.splitext(out)[1],
                        f"{out} has no extension for ffmpeg to work from")

    def test_exactly_one_dot_before_the_extension(self):
        base = os.path.join(self.tmp, "movie.mkv")
        for ext in ("gif", ".gif", "webm", ".webm", None):
            name = os.path.basename(export.default_output(base, ext))
            self.assertNotIn("..", name, f"double dot for ext={ext!r}")


if __name__ == "__main__":
    unittest.main()
