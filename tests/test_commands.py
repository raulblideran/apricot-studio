"""What ffmpeg command each set of options produces.

These never run ffmpeg -- they assert on the argument list, which is where the
inheritance rules actually live. test_encode.py then proves the commands do what
these say they do.
"""

import unittest

import export
import media
from tests.test_units import make_info

KF = [0.0, 2.0, 4.0, 44.000004, 46.0]


def value_after(args, flag):
    """The argument following `flag`, or None."""
    return args[args.index(flag) + 1] if flag in args else None


def index_of(args, flag):
    return args.index(flag) if flag in args else -1


class LosslessSelection(unittest.TestCase):
    def test_taken_when_the_cut_starts_on_a_keyframe(self):
        plan = export.build(make_info(), 44.000004, 58.0, "/tmp/o.mp4", KF)
        self.assertTrue(plan.lossless)
        self.assertEqual(value_after(plan.args, "-c"), "copy")
        self.assertEqual(plan.label, "stream copy")

    def test_seeks_to_the_stored_keyframe_not_the_requested_time(self):
        # The whole point of the fix: 44.0 in, 44.000004 on the command line.
        plan = export.build(make_info(), 44.0, 58.0, "/tmp/o.mp4", KF)
        self.assertTrue(plan.lossless)
        self.assertEqual(value_after(plan.args, "-ss"), "44.000004")

    def test_duration_measured_from_the_keyframe(self):
        plan = export.build(make_info(), 44.0, 58.0, "/tmp/o.mp4", KF)
        # 58.0 - 44.000004, not 58.0 - 44.0, or the clip runs long.
        self.assertAlmostEqual(float(value_after(plan.args, "-t")), 13.999996, places=5)

    def test_declined_between_keyframes(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertFalse(plan.lossless)
        self.assertIn("libx264", plan.args)

    def test_declined_when_a_size_target_is_set(self):
        options = export.Options(target_bytes=10_000_000)
        plan = export.build(make_info(), 44.000004, 58.0, "/tmp/o.mp4", KF, options)
        self.assertFalse(plan.lossless, "a copy cannot honour a size target")

    def test_declined_for_other_formats(self):
        for fmt in (export.WEBM, export.GIF):
            plan = export.build(make_info(), 44.000004, 58.0, "/tmp/o.x", KF,
                                export.Options(fmt=fmt))
            self.assertFalse(plan.lossless, f"{fmt} cannot be a stream copy")


class SeekStrategy(unittest.TestCase):
    """Regression cover for the 516 ms audio/video desync."""

    def test_splits_the_seek_around_the_input(self):
        plan = export.build(make_info(), 44.5, 58.0, "/tmp/o.mp4", KF)
        i = index_of(plan.args, "-i")
        before = [n for n, a in enumerate(plan.args) if a == "-ss" and n < i]
        after = [n for n, a in enumerate(plan.args) if a == "-ss" and n > i]
        self.assertTrue(before, "needs a coarse seek before -i to stay fast")
        self.assertTrue(after, "needs an exact seek after -i or copied audio desyncs")

    def test_the_two_seeks_add_up_to_the_cut_point(self):
        plan = export.build(make_info(), 44.5, 58.0, "/tmp/o.mp4", KF)
        i = index_of(plan.args, "-i")
        coarse = float(plan.args[[n for n, a in enumerate(plan.args)
                                  if a == "-ss" and n < i][0] + 1])
        fine = float(plan.args[[n for n, a in enumerate(plan.args)
                                if a == "-ss" and n > i][0] + 1])
        self.assertAlmostEqual(coarse + fine, 44.5, places=5)

    def test_no_coarse_seek_needed_near_the_start(self):
        plan = export.build(make_info(), 1.0, 5.0, "/tmp/o.mp4", KF)
        i = index_of(plan.args, "-i")
        self.assertFalse([n for n, a in enumerate(plan.args) if a == "-ss" and n < i])

    def test_duration_is_a_length_not_an_endpoint(self):
        plan = export.build(make_info(), 44.5, 58.0, "/tmp/o.mp4", KF)
        self.assertAlmostEqual(float(value_after(plan.args, "-t")), 13.5, places=5)
        self.assertNotIn("-to", plan.args, "-to after a seek is ambiguous; -t is not")


class Inheritance(unittest.TestCase):
    """Regression cover for the B-frames that made exports play back laggy."""

    def test_matches_a_source_with_no_b_frames(self):
        plan = export.build(make_info(has_b_frames=0), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-bf"), "0")

    def test_matches_a_source_that_has_them(self):
        plan = export.build(make_info(has_b_frames=2), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-bf"), "2")

    def test_b_frames_inherited_under_a_size_target_too(self):
        options = export.Options(target_bytes=10_000_000)
        plan = export.build(make_info(has_b_frames=0), 45.0, 58.0, "/tmp/o.mp4",
                            KF, options)
        self.assertEqual(value_after(plan.args, "-bf"), "0")

    def test_hevc_takes_b_frames_through_x265_params(self):
        plan = export.build(make_info(v_codec="hevc", profile="Main"),
                            45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertIn("bframes=0", value_after(plan.args, "-x265-params"))

    def test_carries_colour_metadata(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-color_primaries"), "bt709")
        self.assertEqual(value_after(plan.args, "-color_trc"), "bt709")
        self.assertEqual(value_after(plan.args, "-colorspace"), "bt709")
        self.assertEqual(value_after(plan.args, "-color_range"), "tv")

    def test_omits_colour_tags_the_source_does_not_have(self):
        plan = export.build(make_info(color_space="", color_primaries="unknown"),
                            45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertNotIn("-colorspace", plan.args)
        self.assertNotIn("-color_primaries", plan.args)

    def test_carries_pixel_format_and_profile(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-pix_fmt"), "yuv420p")
        self.assertEqual(value_after(plan.args, "-profile:v"), "high")

    def test_gop_derived_from_measured_keyframes(self):
        two_second = [i * 2.0 for i in range(20)]
        plan = export.build(make_info(fps=60.0), 45.0, 58.0, "/tmp/o.mp4", two_second)
        self.assertEqual(value_after(plan.args, "-g"), "120")

    def test_gop_falls_back_when_keyframes_are_unknown(self):
        plan = export.build(make_info(fps=60.0), 45.0, 58.0, "/tmp/o.mp4", [])
        self.assertEqual(value_after(plan.args, "-g"), "120")

    def test_preserves_variable_frame_timing(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-fps_mode"), "passthrough")

    def test_audio_is_copied_never_re_encoded(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-c:a"), "copy")


class AudioMapping(unittest.TestCase):
    TWO = make_info(audio=(
        media.AudioTrack(0, "opus", 100_000, 48000, 2, "", "Desktop"),
        media.AudioTrack(1, "opus", 90_000, 48000, 1, "", "Mic")))

    def test_all_tracks_by_default(self):
        plan = export.build(self.TWO, 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertIn("0:a", plan.args)

    def test_a_single_track_can_be_chosen(self):
        plan = export.build(self.TWO, 45.0, 58.0, "/tmp/o.mp4", KF,
                            export.Options(audio=1))
        self.assertIn("0:a:1", plan.args)
        self.assertNotIn("0:a", [a for a in plan.args if a == "0:a"])

    def test_mute_maps_nothing(self):
        plan = export.build(self.TWO, 45.0, 58.0, "/tmp/o.mp4", KF,
                            export.Options(audio=export.NO_AUDIO))
        self.assertFalse([a for a in plan.args if a.startswith("0:a")])

    def test_silent_source_maps_nothing(self):
        plan = export.build(make_info(audio=()), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertFalse([a for a in plan.args if a.startswith("0:a")])
        self.assertIn("0:v:0", plan.args)


class Formats(unittest.TestCase):
    def test_gif_builds_its_own_palette(self):
        plan = export.build(make_info(), 45.0, 48.0, "/tmp/o.gif", KF,
                            export.Options(fmt=export.GIF))
        chain = value_after(plan.args, "-filter_complex")
        self.assertIn("palettegen", chain)
        self.assertIn("paletteuse", chain)
        self.assertIn("-an", plan.args)

    def test_gif_does_not_fight_the_filter_chain(self):
        plan = export.build(make_info(), 45.0, 48.0, "/tmp/o.gif", KF,
                            export.Options(fmt=export.GIF))
        for flag in ("-pix_fmt", "-g", "-fps_mode", "-map"):
            self.assertNotIn(flag, plan.args,
                             f"{flag} would conflict with the palette filter")

    def test_webm_uses_vp9(self):
        plan = export.build(make_info(), 45.0, 48.0, "/tmp/o.webm", KF,
                            export.Options(fmt=export.WEBM))
        self.assertEqual(value_after(plan.args, "-c:v"), "libvpx-vp9")

    def test_webm_copies_opus_rather_than_re_encoding_it(self):
        plan = export.build(make_info(), 45.0, 48.0, "/tmp/o.webm", KF,
                            export.Options(fmt=export.WEBM))
        self.assertEqual(value_after(plan.args, "-c:a"), "copy")

    def test_webm_converts_audio_it_cannot_carry(self):
        aac = make_info(audio=(
            media.AudioTrack(0, "aac", 128_000, 48000, 2, "", ""),))
        plan = export.build(aac, 45.0, 48.0, "/tmp/o.webm", KF,
                            export.Options(fmt=export.WEBM))
        self.assertEqual(value_after(plan.args, "-c:a"), "libopus")

    def test_faststart_only_for_mp4_family(self):
        self.assertIn("-movflags",
                      export.build(make_info(ext="mp4"), 45.0, 58.0, "/tmp/o.mp4", KF).args)
        self.assertNotIn("-movflags",
                         export.build(make_info(ext="mkv"), 45.0, 58.0, "/tmp/o.mkv", KF).args)
        self.assertNotIn("-movflags",
                         export.build(make_info(), 45.0, 48.0, "/tmp/o.webm", KF,
                                      export.Options(fmt=export.WEBM)).args)


class SizeTargetCeiling(unittest.TestCase):
    """Regression cover for a 10 MB target that produced 10.3 MB.

    Solving the average bitrate from the budget is not enough. Single-pass ABR
    overshoots on hard content and sits at `-maxrate`, so the *cap* is what
    decides whether the file fits. The invariant is: if the encoder spent maxrate
    for the entire clip, the result would still be under the limit.

    Easy footage never reveals this -- the encoder undershoots and any config
    looks fine -- which is exactly why this is asserted arithmetically rather
    than by encoding a sample.
    """

    DURATION = 14.0

    def worst_case_bytes(self, info, target_mb):
        options = export.Options(target_bytes=target_mb * 1_000_000)
        plan = export.build(info, 45.0, 45.0 + self.DURATION, "/tmp/o.mp4", KF, options)
        maxrate = int(value_after(plan.args, "-maxrate"))
        audio = sum(t.bitrate or 128_000 for t in info.audio)
        return (maxrate + audio) * self.DURATION / 8

    def test_ceiling_fits_the_budget(self):
        info = make_info()
        for mb in (10, 25, 50, 100):
            with self.subTest(mb=mb):
                self.assertLessEqual(
                    self.worst_case_bytes(info, mb), mb * 1_000_000,
                    "an encoder pinned at -maxrate would exceed the limit")

    def test_ceiling_fits_with_no_audio_to_subtract(self):
        info = make_info(audio=())
        self.assertLessEqual(self.worst_case_bytes(info, 10), 10_000_000)

    def test_ceiling_fits_with_several_loud_tracks(self):
        info = make_info(audio=(
            media.AudioTrack(0, "opus", 320_000, 48000, 2, "", ""),
            media.AudioTrack(1, "opus", 256_000, 48000, 2, "", "")))
        self.assertLessEqual(self.worst_case_bytes(info, 10), 10_000_000)

    def test_buffer_is_not_large_enough_to_bank_an_overshoot(self):
        # A VBV buffer bigger than the rate lets the encoder average above the
        # cap on short clips, which is how the original overshoot happened.
        plan = export.build(make_info(), 45.0, 59.0, "/tmp/o.mp4", KF,
                            export.Options(target_bytes=10_000_000))
        rate = int(value_after(plan.args, "-b:v"))
        self.assertLessEqual(int(value_after(plan.args, "-bufsize")), rate)

    def test_no_cap_is_left_unset(self):
        plan = export.build(make_info(), 45.0, 59.0, "/tmp/o.mp4", KF,
                            export.Options(target_bytes=10_000_000))
        self.assertIsNotNone(value_after(plan.args, "-maxrate"),
                             "a size target without a cap is only a suggestion")


class EncoderChoice(unittest.TestCase):
    def test_maps_each_codec_to_its_encoder(self):
        cases = {"h264": "libx264", "hevc": "libx265", "vp9": "libvpx-vp9"}
        for codec, encoder in cases.items():
            self.assertEqual(export.encoder_label(make_info(v_codec=codec)), encoder)

    def test_unknown_codec_lands_on_something_universal(self):
        self.assertEqual(export.encoder_label(make_info(v_codec="wmv3")), "libx264")

    def test_av1_prefers_the_gpu(self):
        # AV1 in software is unusably slow at 4K, which is why this differs.
        label = export.encoder_label(make_info(v_codec="av1", profile="Main"))
        self.assertIn(label, ("av1_vaapi", "libsvtav1"))


class ProgressReporting(unittest.TestCase):
    def test_asks_ffmpeg_for_machine_readable_progress(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-progress"), "pipe:1")
        self.assertIn("-nostats", plan.args)

    def test_lossless_reports_progress_too(self):
        plan = export.build(make_info(), 44.000004, 58.0, "/tmp/o.mp4", KF)
        self.assertEqual(value_after(plan.args, "-progress"), "pipe:1")

    def test_output_path_is_last(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/out.mp4", KF)
        self.assertEqual(plan.args[-1], "/tmp/out.mp4")

    def test_never_prompts(self):
        plan = export.build(make_info(), 45.0, 58.0, "/tmp/o.mp4", KF)
        self.assertIn("-nostdin", plan.args)
        self.assertIn("-y", plan.args)


if __name__ == "__main__":
    unittest.main()
