"""Integration tests: build a command, actually run ffmpeg, inspect the result.

Every assertion here corresponds to a guarantee the app makes about its output.
Several of them exist because that exact guarantee was once quietly broken -- the
clip still played, it was just wrong -- which is the only kind of bug this
project has produced so far.

Slower than the rest of the suite (real encodes), so `run-tests.sh --fast`
skips this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

import export
import media
from tests import fixtures

ONE_FRAME = 1.0 / fixtures.FPS
AUDIO_PACKET = 0.020          # Opus frames are 20 ms; copied audio lands on one


class EncodeCase(unittest.TestCase):
    """Runs exports into a scratch directory that is cleaned up afterwards."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg is required for encode tests")
        cls.tmp = tempfile.mkdtemp(prefix="clipper-encode-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_export(self, kind="allp", start=1.0, end=3.0, options=None, name=None):
        """Export a range and return (plan, output path)."""
        source = fixtures.sample(kind)
        info = media.probe(source)
        options = options or export.Options()
        out = os.path.join(self.tmp,
                           f"{name or self.id().rsplit('.', 1)[-1]}.{options.ext_for(info)}")
        plan = export.build(info, start, end, out, list(fixtures.keyframes(source)),
                            options)
        result = subprocess.run([media.FFMPEG, *plan.args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"ffmpeg failed:\n{result.stderr[-1200:]}")
        self.assertTrue(os.path.exists(out), "ffmpeg reported success but wrote nothing")
        return plan, out


class LosslessCut(EncodeCase):
    """The headline promise: cutting on a keyframe copies bytes, it does not encode."""

    def test_video_is_bit_identical_to_the_source(self):
        source = fixtures.sample("allp")
        keyframe = fixtures.keyframes(source)[1]          # the 2.0s keyframe
        plan, out = self.run_export(start=keyframe, end=keyframe + 2.0)
        self.assertTrue(plan.lossless)
        self.assertEqual(fixtures.video_md5(out),
                         fixtures.video_md5(source, seek=keyframe, length=2.0),
                         "a stream copy must reproduce the source bytes exactly")

    def test_duration_is_what_was_asked_for(self):
        # Regression: asking for 14s once produced 16.02s, because ffmpeg seeks to
        # the keyframe at-or-before the given time and the stored timestamp is not
        # exactly round. Uses a real keyframe, which is what exposed it.
        source = fixtures.sample("allp")
        keyframe = fixtures.keyframes(source)[1]
        _, out = self.run_export(start=keyframe, end=keyframe + 2.0)
        self.assertAlmostEqual(fixtures.stream_duration(out), 2.0, delta=ONE_FRAME)
        # The container runs slightly longer, and legitimately so: a copy cannot
        # split an audio packet at either end, so the clip carries a partial
        # packet at the head and another at the tail. Two packets is the whole
        # honest allowance -- anything beyond that is a real overrun.
        # (It lands exactly on that bound in practice, so compare inclusively.)
        self.assertLessEqual(fixtures.duration(out), 2.0 + 2 * AUDIO_PACKET + 1e-6,
                             "container overran by more than the audio packet grid")

    def test_requesting_a_rounded_time_still_lands_on_the_keyframe(self):
        source = fixtures.sample("allp")
        keyframe = fixtures.keyframes(source)[1]
        self.assertAlmostEqual(keyframe, 2.0, places=3)
        # Ask for the round number, not the stored one.
        _, out = self.run_export(start=2.0, end=4.0)
        self.assertAlmostEqual(fixtures.stream_duration(out), 2.0, delta=ONE_FRAME)

    def test_frame_count_is_exact(self):
        # 2.0s at 60fps is 120 frames, no more: the clearest statement that the
        # copy did not rewind to an earlier keyframe.
        source = fixtures.sample("allp")
        keyframe = fixtures.keyframes(source)[1]
        _, out = self.run_export(start=keyframe, end=keyframe + 2.0)
        self.assertEqual(int(fixtures.stream(out)["nb_frames"]), 2 * fixtures.FPS)

    def test_audio_leads_video_by_at_most_one_packet(self):
        source = fixtures.sample("allp")
        keyframe = fixtures.keyframes(source)[1]
        _, out = self.run_export(start=keyframe, end=keyframe + 2.0)
        video = float(fixtures.stream(out).get("start_time") or 0.0)
        audio = float(fixtures.stream(out, "audio").get("start_time") or 0.0)
        # Audio can only begin on a packet boundary, and video on a frame, so the
        # worst honest case is one of each.
        self.assertLess(abs(video - audio), AUDIO_PACKET + ONE_FRAME,
                        f"A/V drift of {abs(video - audio) * 1000:.0f} ms")

    def test_keeps_the_source_codec_untouched(self):
        source = fixtures.sample("allp")
        keyframe = fixtures.keyframes(source)[1]
        _, out = self.run_export(start=keyframe, end=keyframe + 2.0)
        self.assertEqual(fixtures.stream(out)["codec_name"],
                         fixtures.stream(source)["codec_name"])

    def test_audio_survives_the_copy(self):
        source = fixtures.sample("allp")
        keyframe = fixtures.keyframes(source)[1]
        _, out = self.run_export(start=keyframe, end=keyframe + 2.0)
        self.assertEqual(fixtures.stream(out, "audio").get("codec_name"), "opus")


class ReEncodedCut(EncodeCase):
    def test_duration_is_frame_exact(self):
        _, out = self.run_export(start=1.0, end=3.0)
        self.assertAlmostEqual(fixtures.stream_duration(out), 2.0, delta=2 * ONE_FRAME)

    def test_starts_between_keyframes_without_running_long(self):
        # 1.0s sits mid-GOP; a naive input seek would rewind to 0.0 and add a second.
        plan, out = self.run_export(start=1.0, end=3.0)
        self.assertFalse(plan.lossless)
        self.assertLess(fixtures.duration(out), 2.0 + 4 * ONE_FRAME)

    def test_audio_and_video_start_together(self):
        # Regression: copied audio once began at the preceding keyframe while video
        # began on the exact frame, leaving them 516 ms apart.
        _, out = self.run_export(start=1.0, end=3.0)
        video = float(fixtures.stream(out).get("start_time") or 0.0)
        audio = float(fixtures.stream(out, "audio").get("start_time") or 0.0)
        self.assertLess(abs(video - audio), AUDIO_PACKET,
                        f"A/V drift of {abs(video - audio) * 1000:.0f} ms")

    def test_geometry_and_colour_are_inherited(self):
        source = fixtures.sample("allp")
        _, out = self.run_export(start=1.0, end=3.0)
        got, want = fixtures.stream(out), fixtures.stream(source)
        for key in ("width", "height", "pix_fmt", "r_frame_rate",
                    "color_primaries", "color_transfer", "color_space",
                    "color_range", "profile"):
            self.assertEqual(got.get(key), want.get(key), f"{key} was not inherited")

    def test_frame_count_matches_the_duration(self):
        _, out = self.run_export(start=1.0, end=3.0)
        types = fixtures.frame_types(out, 60)
        self.assertGreater(len(types), 50, "far fewer frames than 2s at 60fps")


class GopStructure(EncodeCase):
    """Regression cover for the exports that played back laggy.

    The source encodes all-P for low latency; an encoder left to itself adds
    B-frames, which forces the decoder to reorder and shows up as stutter.
    """

    def test_all_p_source_produces_an_all_p_clip(self):
        _, out = self.run_export("allp", 1.0, 3.0)
        self.assertEqual(int(fixtures.stream(out)["has_b_frames"]), 0)
        self.assertNotIn("B", fixtures.frame_types(out, 40))

    def test_b_frame_source_keeps_its_b_frames(self):
        # Inheritance has to work in both directions, or it is just a hardcoded 0.
        self.assertEqual(int(fixtures.stream(fixtures.sample("bframes"))["has_b_frames"]), 2)
        _, out = self.run_export("bframes", 1.0, 3.0)
        self.assertGreater(int(fixtures.stream(out)["has_b_frames"]), 0)
        self.assertIn("B", fixtures.frame_types(out, 40))

    def test_structure_holds_under_a_size_target(self):
        _, out = self.run_export("allp", 1.0, 3.0,
                                 export.Options(target_bytes=2_000_000))
        self.assertEqual(int(fixtures.stream(out)["has_b_frames"]), 0)


class SizeTargets(EncodeCase):
    """Regression cover for a 10 MB target that produced 10.3 MB and was rejected."""

    def test_lands_under_the_requested_size(self):
        for mb in (1, 2, 4):
            with self.subTest(mb=mb):
                _, out = self.run_export(
                    "allp", 0.5, 4.5,
                    export.Options(target_bytes=mb * 1_000_000), name=f"size{mb}")
                size = os.path.getsize(out)
                self.assertLessEqual(size, mb * 1_000_000,
                                     f"{size/1e6:.2f} MB exceeds the {mb} MB limit")

    def test_uses_a_worthwhile_share_of_the_budget(self):
        # Landing under is required; landing at 20% would mean needlessly bad quality.
        _, out = self.run_export("allp", 0.5, 4.5,
                                 export.Options(target_bytes=2_000_000))
        self.assertGreater(os.path.getsize(out), 2_000_000 * 0.55)


class AudioSelection(EncodeCase):
    def test_all_tracks_are_kept_by_default(self):
        _, out = self.run_export("twoaudio", 1.0, 3.0)
        streams = [s for s in fixtures.probe_json(out)["streams"]
                   if s["codec_type"] == "audio"]
        self.assertEqual(len(streams), 2, "a second recorded track was dropped")

    def test_a_single_track_can_be_exported(self):
        _, out = self.run_export("twoaudio", 1.0, 3.0,
                                 export.Options(audio=1), name="onetrack")
        streams = [s for s in fixtures.probe_json(out)["streams"]
                   if s["codec_type"] == "audio"]
        self.assertEqual(len(streams), 1)

    def test_mute_produces_no_audio_stream(self):
        _, out = self.run_export("twoaudio", 1.0, 3.0,
                                 export.Options(audio=export.NO_AUDIO), name="muted")
        self.assertEqual(fixtures.stream(out, "audio"), {})

    def test_silent_source_exports_cleanly(self):
        _, out = self.run_export("noaudio", 1.0, 3.0)
        self.assertEqual(fixtures.stream(out, "audio"), {})
        self.assertEqual(fixtures.stream(out)["codec_name"], "h264")

    def test_track_titles_are_read_back(self):
        info = media.probe(fixtures.sample("twoaudio"))
        self.assertEqual(len(info.audio), 2)
        # The fixture stores a raw PipeWire node name, as the real recorder does.
        self.assertEqual(info.audio[0].title, "Desktop")
        self.assertEqual(info.audio[1].title, "Mic")


class Formats(EncodeCase):
    def test_webm_is_vp9(self):
        _, out = self.run_export("allp", 1.0, 2.5,
                                 export.Options(fmt=export.WEBM))
        self.assertEqual(fixtures.stream(out)["codec_name"], "vp9")

    def test_webm_carries_its_audio(self):
        _, out = self.run_export("allp", 1.0, 2.5,
                                 export.Options(fmt=export.WEBM), name="webmaudio")
        self.assertEqual(fixtures.stream(out, "audio").get("codec_name"), "opus")

    def test_gif_is_a_gif_with_real_dimensions(self):
        _, out = self.run_export("allp", 1.0, 2.0, export.Options(fmt=export.GIF))
        stream = fixtures.stream(out)
        self.assertEqual(stream["codec_name"], "gif")
        self.assertEqual(int(stream["width"]), export.GIF_WIDTH)

    def test_gif_has_no_audio(self):
        _, out = self.run_export("allp", 1.0, 2.0, export.Options(fmt=export.GIF),
                                 name="gifsilent")
        self.assertEqual(fixtures.stream(out, "audio"), {})


class Boundaries(EncodeCase):
    def test_cut_from_the_very_beginning(self):
        _, out = self.run_export("allp", 0.0, 2.0, name="fromzero")
        self.assertAlmostEqual(fixtures.stream_duration(out), 2.0, delta=2 * ONE_FRAME)

    def test_cut_running_to_the_end_of_the_file(self):
        source = fixtures.sample("allp")
        total = fixtures.duration(source)
        _, out = self.run_export("allp", total - 1.5, total, name="toend")
        self.assertAlmostEqual(fixtures.stream_duration(out), 1.5, delta=4 * ONE_FRAME)

    def test_a_very_short_cut_still_produces_frames(self):
        _, out = self.run_export("allp", 1.0, 1.2, name="tiny")
        self.assertGreater(fixtures.duration(out), 0.1)


if __name__ == "__main__":
    unittest.main()
