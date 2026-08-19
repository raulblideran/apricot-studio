"""Pure-function tests. No ffmpeg, no Qt event loop, no disk beyond tempfiles."""

import os
import tempfile
import unittest

import export
import media


def make_info(**over) -> media.MediaInfo:
    """A MediaInfo standing in for a screen-recorder capture."""
    fields = dict(
        path="/tmp/sample.mp4", duration=90.0, ext="mp4",
        v_codec="h264", width=2560, height=1440, fps=60.0, pix_fmt="yuv420p",
        profile="High", level=51, v_bitrate=9_507_948, has_b_frames=0,
        color_range="tv", color_primaries="bt709",
        color_transfer="bt709", color_space="bt709",
        audio=(media.AudioTrack(0, "opus", 106_489, 48000, 2, "und", "Desktop"),),
    )
    fields.update(over)
    return media.MediaInfo(**fields)


class Timecode(unittest.TestCase):
    def test_formats_with_milliseconds(self):
        self.assertEqual(media.fmt_tc(0), "00:00:00.000")
        self.assertEqual(media.fmt_tc(42.5), "00:00:42.500")
        self.assertEqual(media.fmt_tc(3661.25), "01:01:01.250")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(media.fmt_tc(-5), "00:00:00.000")

    def test_round_trips(self):
        for value in (0.0, 1.5, 42.5, 90.966, 3661.25):
            self.assertAlmostEqual(media.parse_tc(media.fmt_tc(value)), value,
                                   places=3, msg=f"round trip failed for {value}")

    def test_accepts_short_forms(self):
        self.assertAlmostEqual(media.parse_tc("7.5"), 7.5)
        self.assertAlmostEqual(media.parse_tc("1:02.25"), 62.25)
        self.assertAlmostEqual(media.parse_tc(" 00:00:42.500 "), 42.5)

    def test_rejects_nonsense(self):
        for bad in ("", "   ", "junk", "1:2:3:4", "aa:bb"):
            self.assertIsNone(media.parse_tc(bad), f"{bad!r} should not parse")


class TrackLabels(unittest.TestCase):
    def test_translates_pipewire_device_names(self):
        # gpu-screen-recorder names tracks after the nodes it captured.
        self.assertEqual(media._clean_title("device:default_output|device:default_input"),
                         "Desktop + Mic")
        self.assertEqual(media._clean_title("device:default_output"), "Desktop")

    def test_leaves_ordinary_titles_alone(self):
        self.assertEqual(media._clean_title("Commentary"), "Commentary")
        self.assertEqual(media._clean_title(""), "")

    def test_truncates_runaway_titles(self):
        self.assertLessEqual(len(media._clean_title("x" * 200)), 32)

    def test_label_describes_the_track(self):
        track = media.AudioTrack(0, "opus", 106_489, 48000, 2, "eng", "Desktop")
        self.assertEqual(track.label(), "Track 1 · Desktop — Opus stereo 106k")

    def test_label_survives_missing_metadata(self):
        # Matroska keeps no per-stream bitrate, so this is the common case there.
        bare = media.AudioTrack(1, "opus", 0, 48000, 0, "", "")
        self.assertEqual(bare.label(), "Track 2 — Opus")


class InfoProperties(unittest.TestCase):
    def test_audio_helpers(self):
        info = make_info()
        self.assertTrue(info.has_audio)
        self.assertEqual(info.a_codec, "opus")
        self.assertEqual(info.total_audio_bitrate, 106_489)

    def test_no_audio(self):
        info = make_info(audio=())
        self.assertFalse(info.has_audio)
        self.assertEqual(info.a_codec, "")
        self.assertEqual(info.total_audio_bitrate, 0)

    def test_totals_every_track(self):
        info = make_info(audio=(media.AudioTrack(0, "opus", 100_000, 48000, 2, "", ""),
                                media.AudioTrack(1, "opus", 50_000, 48000, 1, "", "")))
        self.assertEqual(info.total_audio_bitrate, 150_000)

    def test_frame_duration(self):
        self.assertAlmostEqual(make_info(fps=60.0).frame_duration, 1 / 60)
        # A file that reports no frame rate must not divide by zero.
        self.assertGreater(make_info(fps=0.0).frame_duration, 0)

    def test_profile_translated_for_the_encoder(self):
        self.assertEqual(make_info(profile="High").encoder_profile, "high")
        self.assertEqual(make_info(profile="High 10").encoder_profile, "high10")
        self.assertEqual(make_info(v_codec="hevc", profile="Main 10").encoder_profile,
                         "main10")

    def test_unknown_profile_is_omitted_rather_than_guessed(self):
        self.assertEqual(make_info(v_codec="av1", profile="Main").encoder_profile, "")


class OutputNaming(unittest.TestCase):
    def test_adds_clip_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "replay.mp4")
            open(src, "w").close()
            self.assertEqual(export.default_output(src),
                             os.path.join(d, "replay_clip.mp4"))

    def test_never_overwrites_an_existing_clip(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "replay.mp4")
            open(src, "w").close()
            open(os.path.join(d, "replay_clip.mp4"), "w").close()
            open(os.path.join(d, "replay_clip2.mp4"), "w").close()
            self.assertEqual(export.default_output(src),
                             os.path.join(d, "replay_clip3.mp4"))

    def test_honours_a_format_change(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "replay.mp4")
            open(src, "w").close()
            self.assertTrue(export.default_output(src, "gif").endswith("replay_clip.gif"))

    def test_handles_names_with_spaces(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "1v4 on retakes.mp4")
            open(src, "w").close()
            self.assertTrue(export.default_output(src).endswith("1v4 on retakes_clip.mp4"))


class KeyframeMatching(unittest.TestCase):
    """Regression cover for the bug that made lossless clips a GOP too long."""

    KF = [0.0, 2.0, 44.000004, 46.0]

    def test_returns_the_stored_timestamp_not_the_request(self):
        # Asking ffmpeg for 44.0 when the keyframe is at 44.000004 rewinds a
        # whole GOP, so the caller must get the exact stored value back.
        self.assertEqual(export.on_keyframe(44.0, self.KF), 44.000004)

    def test_returns_none_between_keyframes(self):
        self.assertIsNone(export.on_keyframe(45.0, self.KF))

    def test_tolerates_no_keyframes_yet(self):
        # Keyframes load asynchronously; a cut before they arrive must not crash.
        self.assertIsNone(export.on_keyframe(44.0, None))
        self.assertIsNone(export.on_keyframe(44.0, []))

    def test_epsilon_is_tight_enough_to_not_catch_a_frame(self):
        # One frame at 60fps is 16.7ms; the tolerance must be far below that.
        self.assertLess(export.KEYFRAME_EPS, 1 / 60 / 4)

    def test_snap_pulls_within_tolerance_only(self):
        self.assertEqual(export.snap_to_keyframe(44.3, self.KF, 0.5), 44.000004)
        self.assertEqual(export.snap_to_keyframe(44.3, self.KF, 0.1), 44.3)

    def test_snap_without_keyframes_is_identity(self):
        self.assertEqual(export.snap_to_keyframe(44.3, [], 5.0), 44.3)


class SizeTarget(unittest.TestCase):
    """Regression cover for a 10 MB target that produced 10.3 MB."""

    def test_lands_under_the_limit(self):
        info = make_info()
        for mb in (10, 25, 50, 100):
            options = export.Options(target_bytes=mb * 1_000_000)
            rate = export.target_video_bitrate(info, options, 14.0)
            total = (rate + info.total_audio_bitrate) * 14.0 / 8
            self.assertLess(total, mb * 1_000_000,
                            f"{mb} MB target solved to a bitrate that overshoots")

    def test_makes_room_for_audio(self):
        options = export.Options(target_bytes=10_000_000)
        loud = make_info(audio=(media.AudioTrack(0, "opus", 320_000, 48000, 2, "", ""),))
        quiet = make_info(audio=(media.AudioTrack(0, "opus", 64_000, 48000, 2, "", ""),))
        self.assertLess(export.target_video_bitrate(loud, options, 14.0),
                        export.target_video_bitrate(quiet, options, 14.0))

    def test_muted_audio_frees_its_budget(self):
        info = make_info()
        keep = export.Options(target_bytes=10_000_000)
        mute = export.Options(target_bytes=10_000_000, audio=export.NO_AUDIO)
        self.assertGreater(export.target_video_bitrate(info, mute, 14.0),
                           export.target_video_bitrate(info, keep, 14.0))

    def test_never_returns_an_unusable_bitrate(self):
        # An absurd target (1 MB for ten minutes) must still be a valid encode.
        options = export.Options(target_bytes=1_000_000)
        self.assertGreaterEqual(export.target_video_bitrate(make_info(), options, 600.0),
                                100_000)


class OptionsBehaviour(unittest.TestCase):
    def test_extension_follows_the_format(self):
        info = make_info()
        self.assertEqual(export.Options().ext_for(info), "mp4")
        self.assertEqual(export.Options(fmt=export.WEBM).ext_for(info), "webm")
        self.assertEqual(export.Options(fmt=export.GIF).ext_for(info), "gif")

    def test_source_format_keeps_a_matroska_container(self):
        self.assertEqual(export.Options().ext_for(make_info(ext="mkv")), "mkv")

    def test_defaults_inherit_everything(self):
        options = export.Options()
        self.assertEqual(options.fmt, export.SOURCE)
        self.assertEqual(options.audio, export.ALL_AUDIO)
        self.assertEqual(options.target_bytes, 0)


if __name__ == "__main__":
    unittest.main()


class TheApplicationIcon(unittest.TestCase):
    """The icon is drawn by Qt, whose SVG renderer is Tiny 1.2.

    It has no filters at all, and it ignores clipPath outright -- a clipped
    shape paints over the whole canvas rather than being clipped. So a drawing
    that looks right in an editor can arrive in the taskbar looking broken, and
    asserting it here is cheaper than noticing it there.
    """

    def setUp(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = os.path.join(root, "io.github.raulblideran.ApricotStudio.svg")
        if not os.path.exists(self.path):
            self.skipTest("the icon is not installed beside the source")
        with open(self.path, encoding="utf-8") as handle:
            self.svg = handle.read()

    def test_it_is_well_formed(self):
        import xml.etree.ElementTree as ElementTree
        ElementTree.parse(self.path)

    def test_it_scales(self):
        self.assertIn("viewBox", self.svg)

    def test_it_uses_nothing_qt_will_drop_on_the_floor(self):
        # Markup, not raw text: the file explains this constraint in a comment,
        # and a substring search finds its own documentation.
        import xml.etree.ElementTree as ElementTree
        tree = ElementTree.parse(self.path)
        tags = {element.tag.rsplit("}", 1)[-1] for element in tree.iter()}
        for feature in ("filter", "clipPath", "mask", "style"):
            with self.subTest(feature=feature):
                self.assertNotIn(feature, tags,
                                 "Qt's renderer does not honour this")
        attributes = {name for element in tree.iter() for name in element.attrib}
        self.assertNotIn("clip-path", attributes)
