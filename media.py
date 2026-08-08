# Apricot Studio -- a small video trimmer.
# Copyright (C) 2026 Raul Blideran
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file for the full text.
"""Everything Apricot Studio learns about a source file.

A single synchronous ffprobe call fills in MediaInfo (cheap: header read only).
The three expensive extras -- keyframe positions, the audio waveform and the
filmstrip -- run as QProcesses so the UI never blocks waiting for them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from array import array
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QProcess, pyqtSignal
from PyQt6.QtGui import QImage

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

THUMB_W, THUMB_H = 106, 60

# Bucket the waveform decode into roughly this many samples regardless of how
# long the file is, so a two-hour recording costs the same as a 90s clip.
WAVE_SAMPLE_BUDGET = 2_000_000


def fmt_tc(seconds: float) -> str:
    """Seconds -> HH:MM:SS.mmm."""
    # Guard the non-finite cases: int() raises on them, and this runs on every
    # timeline repaint, so one bad value would take the window down.
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return "00:00:00.000"
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_tc(text: str) -> float | None:
    """HH:MM:SS.mmm / MM:SS.mmm / SS.mmm -> seconds. None if unparseable."""
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
    except ValueError:
        return None
    # float() happily accepts "nan" and "inf". A NaN in-point propagates through
    # every clamp (comparisons against NaN are all false) and then crashes the
    # timecode display; a negative one is simply not a position in a file.
    if total != total or total in (float("inf"), float("-inf")) or total < 0:
        return None
    return total


def _num(value, cast, default):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


# ffprobe spells profiles for humans; encoders want their own tokens. Anything
# not listed here is simply not passed through, which lets the encoder choose.
_PROFILES = {
    "h264": {
        "baseline": "baseline",
        "constrained baseline": "baseline",
        "main": "main",
        "high": "high",
        "high 10": "high10",
        "high 4:2:2": "high422",
        "high 4:4:4 predictive": "high444",
    },
    "hevc": {"main": "main", "main 10": "main10", "rext": "mainstillpicture"},
}


# gpu-screen-recorder names its tracks after the PipeWire nodes it captured,
# which is accurate but unreadable in a dropdown.
_DEVICE_NAMES = {"default_output": "Desktop", "default_input": "Mic"}


def _clean_title(title: str) -> str:
    if "device:" not in title and "|" not in title:
        return title if len(title) <= 32 else title[:31] + "…"
    parts = [_DEVICE_NAMES.get(p, p) for p in
             (p.removeprefix("device:") for p in title.split("|")) if p]
    joined = " + ".join(dict.fromkeys(parts))   # de-duplicate, keep order
    return joined if len(joined) <= 32 else joined[:31] + "…"


@dataclass(frozen=True)
class AudioTrack:
    """One audio stream. `index` counts audio streams, so it suits -map 0:a:N."""

    index: int
    codec: str
    bitrate: int
    sample_rate: int
    channels: int
    language: str
    title: str

    def label(self) -> str:
        name = self.title or {"eng": "English", "und": ""}.get(self.language,
                                                               self.language.upper())
        head = f"Track {self.index + 1}" + (f" · {name}" if name else "")
        layout = {1: "mono", 2: "stereo"}.get(self.channels,
                                              f"{self.channels}ch" if self.channels else "")
        detail = " ".join(x for x in (self.codec.title(), layout,
                                      f"{self.bitrate // 1000}k" if self.bitrate else "") if x)
        return f"{head} — {detail}" if detail else head


@dataclass(frozen=True)
class MediaInfo:
    """The subset of a file's parameters that the export inherits."""

    path: str
    duration: float
    ext: str

    v_codec: str
    width: int
    height: int
    fps: float
    pix_fmt: str
    profile: str
    level: int
    v_bitrate: int
    # Reorder depth. Screen recorders encode 0 (all-P) for low latency; an
    # encoder left to its own devices will happily add B-frames the source
    # never had, which costs decode work and reorder delay on playback.
    has_b_frames: int
    color_range: str
    color_primaries: str
    color_transfer: str
    color_space: str

    audio: tuple[AudioTrack, ...] = ()

    # The first track stands in wherever a single answer is wanted.
    @property
    def has_audio(self) -> bool:
        return bool(self.audio)

    @property
    def a_codec(self) -> str:
        return self.audio[0].codec if self.audio else ""

    @property
    def a_bitrate(self) -> int:
        return self.audio[0].bitrate if self.audio else 0

    @property
    def total_audio_bitrate(self) -> int:
        """All tracks together -- what a size target has to make room for."""
        return sum(t.bitrate for t in self.audio)

    @property
    def encoder_profile(self) -> str:
        """The source profile spelled the way the encoder expects, or ''."""
        return _PROFILES.get(self.v_codec, {}).get(self.profile.lower(), "")

    @property
    def frame_duration(self) -> float:
        return 1.0 / self.fps if self.fps > 0 else 1.0 / 30.0

    def summary(self) -> str:
        bits = [f"{self.width}×{self.height}", f"{self.fps:g}fps"]
        codec = self.v_codec.upper().replace("HEVC", "H.265").replace("H264", "H.264")
        bits.append(f"{codec} {self.profile}".strip())
        if self.v_bitrate:
            bits.append(f"{self.v_bitrate / 1_000_000:.1f} Mb/s")
        if not self.has_audio:
            bits.append("no audio")
        else:
            # Matroska keeps no per-stream bitrate, so it is often simply absent.
            rate = f" {self.a_bitrate // 1000}k" if self.a_bitrate else ""
            extra = f" +{len(self.audio) - 1}" if len(self.audio) > 1 else ""
            bits.append(f"{self.a_codec.title()}{rate}{extra}")
        return "  ·  ".join(bits)


def probe(path: str) -> MediaInfo:
    """Read a file's parameters. Raises RuntimeError if it isn't playable video."""
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe could not read this file")

    data = json.loads(result.stdout or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    audio = audio_streams[0] if audio_streams else None
    if video is None:
        raise RuntimeError("no video stream in this file")

    duration = _num(fmt.get("duration"), float, 0.0) or _num(video.get("duration"), float, 0.0)
    if duration <= 0:
        raise RuntimeError("could not determine duration")

    # r_frame_rate is the nominal rate; gpu-screen-recorder writes VFR files
    # where avg_frame_rate is a messy fraction, so prefer the nominal one.
    fps = 30.0
    for key in ("r_frame_rate", "avg_frame_rate"):
        raw = video.get(key, "")
        if "/" in str(raw):
            num, _, den = str(raw).partition("/")
            n, d = _num(num, float, 0.0), _num(den, float, 0.0)
            if n > 0 and d > 0:
                fps = n / d
                break

    tracks = []
    for i, stream in enumerate(audio_streams):
        tags = stream.get("tags") or {}
        tracks.append(AudioTrack(
            index=i,
            codec=stream.get("codec_name", ""),
            bitrate=_num(stream.get("bit_rate"), int, 0),
            sample_rate=_num(stream.get("sample_rate"), int, 0),
            channels=_num(stream.get("channels"), int, 0),
            language=str(tags.get("language", "")),
            title=_clean_title(str(tags.get("title", ""))),
        ))
    a_bitrate = _num(audio.get("bit_rate") if audio else None, int, 0)

    # Stream bitrate is often absent in Matroska; fall back to the container
    # total minus audio, and finally to size/duration.
    v_bitrate = _num(video.get("bit_rate"), int, 0)
    if not v_bitrate:
        total = _num(fmt.get("bit_rate"), int, 0)
        if not total:
            size = _num(fmt.get("size"), int, 0)
            total = int(size * 8 / duration) if size else 0
        v_bitrate = max(total - a_bitrate, 0)

    # Split the basename, not the whole path: a dot in a *directory* name
    # ("~/my.videos/clip") would otherwise make the extension "videos/clip",
    # which then lands in the output filename and the container checks.
    ext = os.path.splitext(os.path.basename(path))[1].lstrip(".").lower() or "mp4"

    return MediaInfo(
        path=path,
        duration=duration,
        ext=ext,
        v_codec=video.get("codec_name", ""),
        width=_num(video.get("width"), int, 0),
        height=_num(video.get("height"), int, 0),
        fps=fps,
        pix_fmt=video.get("pix_fmt", ""),
        profile=str(video.get("profile", "")),
        level=_num(video.get("level"), int, 0),
        v_bitrate=v_bitrate,
        has_b_frames=_num(video.get("has_b_frames"), int, 0),
        audio=tuple(tracks),
        color_range=video.get("color_range", ""),
        color_primaries=video.get("color_primaries", ""),
        color_transfer=video.get("color_transfer", ""),
        color_space=video.get("color_space", ""),
    )


class _Loader(QObject):
    """Shared plumbing for the background probes: start one, cancel the old one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None

    def cancel(self) -> None:
        if self._proc is not None:
            proc, self._proc = self._proc, None
            proc.finished.disconnect()
            proc.readyReadStandardOutput.disconnect()
            proc.kill()
            proc.waitForFinished(500)  # kill() is async; reap before deleting
            proc.deleteLater()

    def _start(self, program: str, args: list[str]) -> QProcess:
        self.cancel()
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        self._proc = proc
        proc.start(program, args)
        return proc

    def _on_output(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _on_finished(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class KeyframeLoader(_Loader):
    """Keyframe timestamps. ~0.85s for a 90s 1440p60 file."""

    ready = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = ""

    def load(self, path: str) -> None:
        self._buf = ""
        self._start(
            FFPROBE,
            ["-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
             "-show_entries", "frame=pts_time", "-of", "csv=p=0", path],
        )

    def _on_output(self) -> None:
        self._buf += bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")

    def _on_finished(self) -> None:
        times = []
        for line in self._buf.splitlines():
            value = _num(line.strip().rstrip(","), float, None)
            if value is not None:
                times.append(value)
        self._proc = None
        self.ready.emit(sorted(times))


class WaveformLoader(_Loader):
    """Peak envelope of the audio, as `buckets` values in 0..1."""

    ready = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pcm = bytearray()
        self._buckets = 0

    def load(self, info: MediaInfo, buckets: int = 2000) -> None:
        self._pcm = bytearray()
        self._buckets = buckets
        if not info.has_audio:
            self.ready.emit([])
            return
        rate = max(400, min(8000, int(WAVE_SAMPLE_BUDGET / max(info.duration, 1.0))))
        self._start(
            FFMPEG,
            ["-v", "error", "-nostdin", "-i", info.path, "-vn",
             "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        )

    def _on_output(self) -> None:
        self._pcm += bytes(self._proc.readAllStandardOutput())

    def _on_finished(self) -> None:
        self._proc = None
        # array('h') needs a whole number of samples; a killed process can leave
        # a dangling byte.
        usable = len(self._pcm) - (len(self._pcm) % 2)
        samples = array("h")
        samples.frombytes(bytes(self._pcm[:usable]))
        self._pcm = bytearray()

        total = len(samples)
        if total == 0:
            self.ready.emit([])
            return

        # max() over a slice runs at C speed, so this stays fast even when the
        # decode produced millions of samples.
        peaks = []
        for i in range(self._buckets):
            lo = total * i // self._buckets
            hi = max(total * (i + 1) // self._buckets, lo + 1)
            chunk = samples[lo:hi]
            peak = max(max(chunk), -min(chunk)) if chunk else 0
            peaks.append(min(peak / 32768.0, 1.0))

        # Game capture often peaks well below full scale, which would draw as a
        # flat line. Scale to the loudest moment so the shape is readable; the
        # relative dynamics that make a gunshot stand out are preserved.
        ceiling = max(peaks, default=0.0)
        if ceiling > 0.02:
            peaks = [min(p / ceiling, 1.0) for p in peaks]
        self.ready.emit(peaks)


class ThumbnailLoader(_Loader):
    """Filmstrip frames, decoding keyframes only so it stays fast.

    Frames arrive as fixed-size raw RGB, so each one is a plain slice with no
    image parsing. They are emitted as they arrive and the strip fills in.
    """

    frame = pyqtSignal(QImage)
    ready = pyqtSignal()

    _FRAME_BYTES = THUMB_W * THUMB_H * 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = bytearray()

    def load(self, path: str) -> None:
        self._buf = bytearray()
        self._start(
            FFMPEG,
            ["-v", "error", "-nostdin", "-skip_frame", "nokey", "-i", path, "-an",
             "-vf", f"scale={THUMB_W}:{THUMB_H}", "-fps_mode", "passthrough",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        )

    def _on_output(self) -> None:
        self._buf += bytes(self._proc.readAllStandardOutput())
        while len(self._buf) >= self._FRAME_BYTES:
            raw = bytes(self._buf[: self._FRAME_BYTES])
            del self._buf[: self._FRAME_BYTES]
            # QImage does not take ownership of the buffer, so copy() before the
            # bytes go out of scope.
            image = QImage(raw, THUMB_W, THUMB_H, THUMB_W * 3,
                           QImage.Format.Format_RGB888).copy()
            self.frame.emit(image)

    def _on_finished(self) -> None:
        self._proc = None
        self._buf = bytearray()
        self.ready.emit()
