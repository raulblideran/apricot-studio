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


class QualityPresets(unittest.TestCase):
    """Presets scale against the source, so they mean the same for any file."""

    def args_for(self, quality, **info_over):
        options = export.Options(quality=quality)
        return export.build(make_info(**info_over), 45.0, 59.0,
                            "/tmp/o.mp4", KF, options).args

    def test_source_quality_caps_at_the_source_bitrate(self):
        info = make_info()
        args = self.args_for(export.SOURCE_QUALITY)
        self.assertEqual(int(value_after(args, "-maxrate")), info.v_bitrate)

    def test_each_step_down_lowers_the_ceiling(self):
        # Strictly lower, not merely non-increasing: presets that all resolve to
        # the same ceiling would satisfy a sorted() check while doing nothing.
        full, smaller, smallest = (
            int(value_after(self.args_for(q), "-maxrate"))
            for q in (export.SOURCE_QUALITY, export.SMALLER, export.SMALLEST))
        self.assertLess(smaller, full)
        self.assertLess(smallest, smaller)

    def test_each_step_down_raises_crf(self):
        full, smaller, smallest = (
            int(value_after(self.args_for(q), "-crf"))
            for q in (export.SOURCE_QUALITY, export.SMALLER, export.SMALLEST))
        self.assertLess(full, smaller)
        self.assertLess(smaller, smallest)

    def test_the_presets_deliver_what_their_labels_promise(self):
        # The dropdown tells the user "about half the bitrate" and "roughly a
        # third". Those are claims, so pin them to literals -- comparing against
        # QUALITY_RATE would just be reading the implementation back to itself
        # and would accept a preset that quietly stopped meaning anything.
        self.assertEqual(export.QUALITY_RATE[export.SOURCE_QUALITY], 1.0)
        self.assertAlmostEqual(export.QUALITY_RATE[export.SMALLER], 0.5, delta=0.1)
        self.assertAlmostEqual(export.QUALITY_RATE[export.SMALLEST], 1 / 3, delta=0.1)

    def test_the_presets_are_meaningfully_apart(self):
        # Steps a user cannot tell apart are worse than no steps at all.
        rates = [export.QUALITY_RATE[q] for q in
                 (export.SOURCE_QUALITY, export.SMALLER, export.SMALLEST)]
        for higher, lower in zip(rates, rates[1:]):
            self.assertLess(lower, higher * 0.8,
                            "consecutive presets are too close to distinguish")

    def test_the_ceiling_is_a_share_of_this_file_not_a_fixed_number(self):
        # The same preset on a 2 Mb/s source and a 40 Mb/s one must differ.
        small = int(value_after(self.args_for(export.SMALLER, v_bitrate=2_000_000),
                                "-maxrate"))
        large = int(value_after(self.args_for(export.SMALLER, v_bitrate=40_000_000),
                                "-maxrate"))
        self.assertAlmostEqual(large / small, 20, delta=0.5)

    def test_b_frames_still_inherited_at_every_preset(self):
        for quality in (export.SOURCE_QUALITY, export.SMALLER, export.SMALLEST):
            self.assertEqual(value_after(self.args_for(quality, has_b_frames=0), "-bf"), "0")
            self.assertEqual(value_after(self.args_for(quality, has_b_frames=2), "-bf"), "2")

    def test_hevc_offsets_from_its_own_baseline(self):
        args = self.args_for(export.SMALLER, v_codec="hevc", profile="Main")
        self.assertEqual(int(value_after(args, "-crf")),
                         export.CRF["hevc"] + export.QUALITY_CRF[export.SMALLER])

    def test_a_reduced_preset_rules_out_a_stream_copy(self):
        # A copy reproduces the source, which is not what "smaller" asked for.
        for quality in (export.SMALLER, export.SMALLEST):
            plan = export.build(make_info(), 44.000004, 58.0, "/tmp/o.mp4", KF,
                                export.Options(quality=quality))
            self.assertFalse(plan.lossless, f"{quality} must not stream-copy")

    def test_source_quality_still_allows_a_copy(self):
        plan = export.build(make_info(), 44.000004, 58.0, "/tmp/o.mp4", KF,
                            export.Options(quality=export.SOURCE_QUALITY))
        self.assertTrue(plan.lossless)

    def test_inherits_everything_is_exact_about_what_it_claims(self):
        self.assertTrue(export.Options().inherits_everything)
        self.assertFalse(export.Options(quality=export.SMALLER).inherits_everything)
        self.assertFalse(export.Options(target_bytes=10_000_000).inherits_everything)
        self.assertFalse(export.Options(fmt=export.WEBM).inherits_everything)


class SizeEstimates(unittest.TestCase):
    """Shown before an export runs, so it has to be roughly honest."""

    DURATION = 14.0

    def estimate(self, **over):
        return export.estimate_bytes(make_info(), export.Options(**over), self.DURATION)

    def test_source_quality_estimates_near_the_source_bitrate(self):
        info = make_info()
        expected = (info.v_bitrate + info.total_audio_bitrate) * self.DURATION / 8
        self.assertAlmostEqual(self.estimate(), expected, delta=expected * 0.02)

    def test_smaller_presets_estimate_smaller(self):
        full = self.estimate()
        smaller = self.estimate(quality=export.SMALLER)
        smallest = self.estimate(quality=export.SMALLEST)
        self.assertLess(smaller, full)
        self.assertLess(smallest, smaller)

    def test_the_estimate_tracks_the_preset_it_was_given(self):
        # Roughly half and roughly a third, not just "some smaller number".
        full = self.estimate()
        self.assertAlmostEqual(self.estimate(quality=export.SMALLER) / full,
                               export.QUALITY_RATE[export.SMALLER], delta=0.06)
        self.assertAlmostEqual(self.estimate(quality=export.SMALLEST) / full,
                               export.QUALITY_RATE[export.SMALLEST], delta=0.06)

    def test_a_size_target_estimates_just_under_the_limit(self):
        estimate = self.estimate(target_bytes=10_000_000)
        self.assertLessEqual(estimate, 10_000_000)
        self.assertGreater(estimate, 8_000_000)

    def test_muting_audio_lowers_the_estimate(self):
        self.assertLess(self.estimate(audio=export.NO_AUDIO), self.estimate())

    def test_gif_declines_to_guess(self):
        # Palette output does not follow from a bitrate, so no number is better
        # than a wrong one.
        self.assertEqual(self.estimate(fmt=export.GIF), 0)

    def test_zero_length_selection_estimates_nothing(self):
        self.assertEqual(export.estimate_bytes(make_info(), export.Options(), 0.0), 0)

    def test_estimate_scales_with_the_selection(self):
        info, options = make_info(), export.Options()
        short = export.estimate_bytes(info, options, 5.0)
        long = export.estimate_bytes(info, options, 50.0)
        self.assertAlmostEqual(long / short, 10, delta=0.1)


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
