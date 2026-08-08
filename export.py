"""Turning an in/out selection into an ffmpeg command, and running it.

Every encoding parameter here is derived from the source file. There is
deliberately nothing for the user to configure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from media import FFMPEG, MediaInfo

VAAPI_DEVICE = "/dev/dri/renderD128"

# Capped CRF: quality drives the encode, but a one-second VBV buffer pinned to
# the source's own bitrate stops it running away. Easy footage comes in under
# the source's size, hard footage lands right on it -- so the output inherits
# the source's bitrate without ever being told a target.
#
# Measured on a 1440p60 replay (source 9.51 Mb/s): this lands at 9.58 Mb/s,
# 1.00x the source, at 37.5 dB PSNR against it. Dropping to CRF 24 saves nothing
# on size (both hit the cap) and costs 1.8 dB, which is why the floor is 20.
CRF = {"h264": 20, "hevc": 22}

# Quality presets. Each pairs a CRF offset with a ceiling expressed as a share of
# the source's own bitrate, so "smaller" means smaller *than this file* rather
# than some absolute number that would mean different things for a 720p capture
# and a 4K one. The cap does most of the work; the CRF offset stops easy footage
# from spending the whole budget on nothing.
SOURCE_QUALITY, SMALLER, SMALLEST = "source", "smaller", "smallest"
QUALITY_CRF = {SOURCE_QUALITY: 0, SMALLER: 4, SMALLEST: 8}
QUALITY_RATE = {SOURCE_QUALITY: 1.00, SMALLER: 0.50, SMALLEST: 0.30}

# VP9 at the settings used here lands well under an equivalent H.264 bitrate;
# only used to estimate a size before encoding.
WEBM_RATE_FACTOR = 0.6

# Matching the source's GOP structure means giving up B-frames on screen-recorder
# footage, which costs efficiency; a slower preset buys it back. Measured on a
# 14s 1440p60 clip: veryfast 5.0s/36.41 dB, fast 9.3s/37.35 dB, medium
# 11.2s/37.45 dB -- medium adds 2s for 0.1 dB, so `fast` is where it stops
# paying. x265 is far slower at the same preset name, so it keeps veryfast.
PRESET = {"h264": "fast", "hevc": "veryfast"}

# Fast-seek to this many seconds before the cut, then seek the rest of the way
# accurately. Stream-copied audio can only start on a whole packet, so seeking
# the whole distance on the input would start the audio at the preceding
# keyframe while video started on the exact frame -- a half-second of desync.
SEEK_MARGIN = 10.0


# A cut can be stream-copied only if it starts exactly on a keyframe. The UI
# snaps to them, so this compares against the same value it snapped to.
KEYFRAME_EPS = 0.002

SOURCE, WEBM, GIF = "source", "webm", "gif"
ALL_AUDIO, NO_AUDIO = "all", "none"

GIF_WIDTH, GIF_FPS = 480, 15

# A size target is usually an upload limit, so overshooting is a failure, not a
# rounding error. Single-pass ABR overshoots by a few percent on its own, so aim
# under and cap hard (measured: 0.97 with a loose cap landed 10.3 MB on a 10 MB
# target -- rejected by Discord).
SIZE_MARGIN = 0.90

# Sanity bounds for arithmetic that divides by a caller-supplied duration.
MIN_DURATION = 0.001
MAX_BITRATE = 800_000_000      # far above any real source; catches divide-by-tiny


@dataclass(frozen=True)
class Options:
    """The few things that are allowed to deviate from the source."""

    fmt: str = SOURCE                  # SOURCE | WEBM | GIF
    audio: str | int = ALL_AUDIO       # ALL_AUDIO | NO_AUDIO | track index
    target_bytes: int = 0              # 0 = quality-driven rather than size-driven
    quality: str = SOURCE_QUALITY      # SOURCE_QUALITY | SMALLER | SMALLEST

    def ext_for(self, info: MediaInfo) -> str:
        return {WEBM: "webm", GIF: "gif"}.get(self.fmt, info.ext)

    @property
    def inherits_everything(self) -> bool:
        """Whether this reproduces the source rather than deviating from it."""
        return (self.fmt == SOURCE and not self.target_bytes
                and self.quality == SOURCE_QUALITY)


@dataclass(frozen=True)
class Plan:
    """A fully resolved ffmpeg invocation, plus what to tell the user about it."""

    args: list[str]
    output: str
    duration: float
    label: str
    lossless: bool = False


def default_output(path: str, ext: str | None = None) -> str:
    """`clip.mp4` -> `clip_clip.mp4`, avoiding names that already exist."""
    stem, source_ext = os.path.splitext(path)
    # A source with no extension would otherwise produce a destination with none
    # either, leaving ffmpeg no way to pick a muxer.
    source_ext = f".{ext.lstrip('.')}" if ext else (source_ext or ".mp4")
    candidate = f"{stem}_clip{source_ext}"
    n = 2
    while os.path.exists(candidate):
        candidate = f"{stem}_clip{n}{source_ext}"
        n += 1
    return candidate


def on_keyframe(start: float, keyframes: list[float] | None) -> float | None:
    """The exact keyframe this cut starts on, or None if it starts between two.

    The exact stored timestamp matters: ffmpeg seeks to the keyframe at or
    *before* the time it is given, so asking for 44.0 when the keyframe really
    sits at 44.000004 rewinds a whole GOP and the clip comes out too long.
    """
    for k in keyframes or []:
        if abs(k - start) <= KEYFRAME_EPS:
            return k
    return None


def snap_to_keyframe(t: float, keyframes: list[float] | None,
                     tolerance: float) -> float:
    """The nearest keyframe within `tolerance`, else `t` unchanged."""
    if not keyframes:
        return t
    nearest = min(keyframes, key=lambda k: abs(k - t))
    return nearest if abs(nearest - t) <= tolerance else t


def _audio_maps(info: MediaInfo, options: Options) -> list[str]:
    if not info.has_audio or options.audio == NO_AUDIO or options.fmt == GIF:
        return []
    if options.audio == ALL_AUDIO:
        # Capture setups often record mic and desktop to separate tracks, and
        # dropping one is silent.
        return ["-map", "0:a"]
    return ["-map", f"0:a:{int(options.audio)}"]


def _selected_audio_bitrate(info: MediaInfo, options: Options) -> int:
    """Bitrate of whichever tracks survive, for size-target arithmetic."""
    if not info.has_audio or options.audio == NO_AUDIO or options.fmt == GIF:
        return 0
    tracks = (info.audio if options.audio == ALL_AUDIO
              else [t for t in info.audio if t.index == int(options.audio)])
    # Matroska often omits per-stream bitrate; assume a typical stereo stream.
    return sum(t.bitrate or 128_000 for t in tracks)


def target_video_bitrate(info: MediaInfo, options: Options, duration: float) -> int:
    """Solve backwards from a wanted file size to a video bitrate."""
    budget_bits = options.target_bytes * 8 * SIZE_MARGIN
    audio_bits = _selected_audio_bitrate(info, options) * duration
    video_bps = (budget_bits - audio_bits) / max(duration, MIN_DURATION)
    # Floor: below this the clip is unwatchable anyway. Ceiling: a near-zero
    # duration would otherwise solve to an absurd rate that no encoder accepts.
    return max(min(int(video_bps), MAX_BITRATE), 100_000)


def estimate_bytes(info: MediaInfo, options: Options, duration: float) -> int:
    """Roughly how large the export will be, for showing before it runs.

    An estimate, not a promise: quality-driven encodes come in under their
    ceiling on easy footage. A size target is the one case that is close to
    exact, because the bitrate was solved from it.
    """
    if duration <= 0:
        return 0
    if options.fmt == GIF:
        return 0                      # palette output is not predictable enough
    if options.target_bytes:
        return int(options.target_bytes * SIZE_MARGIN)

    audio_bps = _selected_audio_bitrate(info, options)
    if options.fmt == WEBM:
        video_bps = info.v_bitrate * WEBM_RATE_FACTOR
    else:
        video_bps = info.v_bitrate * QUALITY_RATE.get(options.quality, 1.0)
    return int((video_bps + audio_bps) * duration / 8)


def _gop_size(info: MediaInfo, keyframes: list[float]) -> int:
    """The source's keyframe interval in frames, so the clip behaves like it."""
    if len(keyframes) >= 3:
        gaps = sorted(b - a for a, b in zip(keyframes, keyframes[1:]))
        median = gaps[len(gaps) // 2]
        if 0.1 < median < 30:
            return max(1, round(median * info.fps))
    return max(1, round(2.0 * info.fps))


def _video_args(info: MediaInfo, options: Options = Options(),
                duration: float = 0.0) -> tuple[list[str], list[str], str]:
    """Returns (args before -i, args after -i, human label)."""
    if options.fmt == GIF:
        # One pass: build a palette from the clip and apply it, which is the
        # difference between a GIF that looks like the video and one that doesn't.
        chain = (f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,"
                 f"split[a][b];[a]palettegen=stats_mode=diff[p];"
                 f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        return [], ["-filter_complex", chain, "-loop", "0"], "gif"

    if options.fmt == WEBM:
        # No GPU here encodes VP9, so this is software and genuinely slow.
        return [], ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
                    "-deadline", "good", "-cpu-used", "4"], "libvpx-vp9"

    codec = info.v_codec

    if options.target_bytes:
        # A size target replaces quality-driven rate control with an average
        # bitrate solved from the budget.
        rate = target_video_bitrate(info, options, duration)
        rate_args = ["-b:v", str(rate), "-maxrate", str(int(rate * 1.05)),
                     "-bufsize", str(rate)]
        if codec == "hevc":
            return [], ["-c:v", "libx265", "-preset", PRESET["hevc"], "-x265-params",
                        f"log-level=error:bframes={info.has_b_frames}",
                        *rate_args], "libx265"
        return [], ["-c:v", "libx264", "-preset", PRESET["h264"],
                    "-bf", str(info.has_b_frames), *rate_args], "libx264"

    # A ceiling expressed as a share of the source's own bitrate, so a preset
    # means the same thing whatever the file is, plus one second of VBV.
    share = QUALITY_RATE.get(options.quality, 1.0)
    ceiling = int(info.v_bitrate * share)
    vbv = ["-maxrate", str(ceiling), "-bufsize", str(ceiling)] if ceiling else []
    offset = QUALITY_CRF.get(options.quality, 0)

    if codec == "h264":
        args = ["-c:v", "libx264", "-preset", PRESET["h264"],
                "-crf", str(CRF["h264"] + offset),
                "-bf", str(info.has_b_frames), *vbv]
        if info.encoder_profile:
            args += ["-profile:v", info.encoder_profile]
        return [], args, "libx264"

    if codec == "hevc":
        # x265 is loud on stderr by default, and its CRF scale sits a couple of
        # points above x264's for equivalent quality. It takes its B-frame count
        # through x265-params rather than -bf.
        args = ["-c:v", "libx265", "-preset", PRESET["hevc"],
                "-crf", str(CRF["hevc"] + offset),
                "-x265-params", f"log-level=error:bframes={info.has_b_frames}", *vbv]
        if info.encoder_profile:
            args += ["-profile:v", info.encoder_profile]
        return [], args, "libx265"

    if codec == "vp9":
        return [], ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
                    "-row-mt", "1", "-deadline", "good", "-cpu-used", "2"], "libvpx-vp9"

    if codec == "av1":
        # AV1 in software is far too slow for 4K sources; this GPU encodes it
        # natively, and it is what the machine's own convert script already uses.
        if os.path.exists(VAAPI_DEVICE):
            surface = "p010" if "10" in info.pix_fmt else "nv12"
            return (["-vaapi_device", VAAPI_DEVICE],
                    ["-vf", f"format={surface},hwupload", "-c:v", "av1_vaapi", "-qp", "28"],
                    "av1_vaapi")
        return [], ["-c:v", "libsvtav1", "-preset", "8", "-crf", "30"], "libsvtav1"

    # Unknown codec: fall back to H.264, which every player handles.
    return [], ["-c:v", "libx264", "-preset", PRESET["h264"],
                "-crf", str(CRF["h264"] + offset),
                "-bf", str(info.has_b_frames), *vbv], "libx264"


def encoder_label(info: MediaInfo, options: Options = Options()) -> str:
    """Which encoder this file will be re-encoded with."""
    return _video_args(info, options, 1.0)[2]


def _build_lossless(info: MediaInfo, start: float, duration: float, output: str,
                    options: Options) -> Plan:
    """A pure stream copy. Only valid when the cut starts on a keyframe."""
    args = ["-hide_banner", "-nostdin", "-y",
            # Safe to seek the whole way on the input here: the start is a
            # keyframe, so there is no partial GOP for audio to disagree about.
            "-ss", f"{start:.6f}", "-i", info.path, "-t", f"{duration:.6f}",
            "-map", "0:v:0", *_audio_maps(info, options),
            "-c", "copy", "-avoid_negative_ts", "make_zero"]
    if info.ext in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
    args += ["-stats_period", "0.25", "-progress", "pipe:1", "-nostats", output]
    return Plan(args=args, output=output, duration=duration,
                label="stream copy", lossless=True)


def build(info: MediaInfo, start: float, end: float, output: str,
          keyframes: list[float] | None = None,
          options: Options = Options()) -> Plan:
    """Assemble the cut command for [start, end)."""
    # A caller can hand over anything; keep the arithmetic inside the file.
    start = min(max(start, 0.0), info.duration)
    duration = max(end - start, info.frame_duration)

    # Starting exactly on a keyframe means nothing has to be decoded at all.
    # A copy reproduces the source exactly, so it is only valid when nothing was
    # asked to differ from it -- a smaller-quality preset rules it out too.
    if options.inherits_everything:
        exact = on_keyframe(start, keyframes)
        if exact is not None:
            return _build_lossless(info, exact, end - exact, output, options)

    gop = _gop_size(info, keyframes or [])
    pre, video, label = _video_args(info, options, duration)

    # Split the seek: cheap keyframe seek on the input, exact seek on the output.
    # The output-side seek trims every stream to the same instant, which is what
    # keeps copied audio lined up with the re-encoded video.
    coarse = max(0.0, start - SEEK_MARGIN)
    fine = start - coarse

    args = ["-hide_banner", "-nostdin", "-y", *pre]
    if coarse > 0:
        args += ["-ss", f"{coarse:.6f}"]
    args += ["-i", info.path, "-ss", f"{fine:.6f}", "-t", f"{duration:.6f}"]
    if options.fmt != GIF:
        # filter_complex does its own stream selection, so -map would clash.
        args += ["-map", "0:v:0", *_audio_maps(info, options)]

    args += video

    if options.fmt == GIF:
        # paletteuse emits pal8 and the fps filter sets the rate, so pinning
        # pixel format, GOP, colour or frame timing here would fight the chain.
        args += ["-an"]
    else:
        # Hardware frames live on the GPU in their own format, so -pix_fmt would
        # fight the hwupload filter.
        if "hwupload" not in " ".join(video) and info.pix_fmt:
            args += ["-pix_fmt", "yuv420p" if options.fmt == WEBM else info.pix_fmt]

        if gop and options.fmt != WEBM:
            args += ["-g", str(gop)]

        for flag, value in (("-color_primaries", info.color_primaries),
                            ("-color_trc", info.color_transfer),
                            ("-colorspace", info.color_space),
                            ("-color_range", info.color_range)):
            if value and value != "unknown":
                args += [flag, value]

        # Sources from screen recorders are variable-frame-rate; passthrough keeps
        # their original timestamps instead of resampling to a constant rate.
        args += ["-fps_mode", "passthrough", "-avoid_negative_ts", "make_zero"]

        if _audio_maps(info, options):
            if options.fmt == WEBM and info.a_codec != "opus":
                # WebM carries only Opus or Vorbis.
                args += ["-c:a", "libopus", "-b:a", str(info.a_bitrate or 128_000)]
            else:
                # Audio never constrains where the video can be cut, so copying it
                # keeps it bit-identical and spends the generation of loss on video.
                args += ["-c:a", "copy"]

        if options.fmt == SOURCE and info.ext in ("mp4", "mov", "m4v"):
            args += ["-movflags", "+faststart"]

    # Default stats come once a second, which makes the bar lurch.
    args += ["-stats_period", "0.25", "-progress", "pipe:1", "-nostats", output]
    return Plan(args=args, output=output, duration=duration, label=label)


class Exporter(QObject):
    """Runs a Plan, reporting progress parsed from ffmpeg's -progress stream."""

    progress = pyqtSignal(float, float, float)  # fraction, speed, eta seconds
    finished = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._plan: Plan | None = None
        self._stderr = ""
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self._proc is not None

    def start(self, plan: Plan) -> None:
        if self._proc is not None:
            return
        self._plan = plan
        self._stderr = ""
        self._cancelled = False
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._on_progress)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start(FFMPEG, plan.args)

    def cancel(self) -> None:
        if self._proc is not None:
            self._cancelled = True
            self._proc.kill()

    def _on_stderr(self) -> None:
        self._stderr += bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")

    def _on_progress(self) -> None:
        text = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        done = speed = None
        for line in text.splitlines():
            key, _, value = line.partition("=")
            value = value.strip()
            if key == "out_time_us" and value.lstrip("-").isdigit():
                done = int(value) / 1_000_000
            elif key == "speed" and value.endswith("x"):
                try:
                    speed = float(value[:-1])
                except ValueError:
                    pass
        if done is None or not self._plan:
            return
        fraction = min(max(done / self._plan.duration, 0.0), 1.0)
        speed = speed or 0.0
        remaining = self._plan.duration - done
        eta = remaining / speed if speed > 0 else 0.0
        self.progress.emit(fraction, speed, max(eta, 0.0))

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._proc = None
            self.finished.emit(False, "could not run ffmpeg")

    def _on_finished(self, code: int, status) -> None:
        plan, self._plan = self._plan, None
        self._proc = None
        if self._cancelled:
            if plan and os.path.exists(plan.output):
                try:
                    os.remove(plan.output)  # a killed encode leaves a broken file
                except OSError:
                    pass
            self.finished.emit(False, "Export cancelled")
            return
        if code == 0 and plan and os.path.exists(plan.output):
            size = os.path.getsize(plan.output) / 1_000_000
            self.finished.emit(True, f"Saved {os.path.basename(plan.output)}  ·  {size:.0f} MB")
            return
        tail = [ln for ln in self._stderr.strip().splitlines() if ln.strip()]
        self.finished.emit(False, tail[-1] if tail else f"ffmpeg failed (exit {code})")
