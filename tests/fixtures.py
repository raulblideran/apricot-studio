"""Synthetic test media.

The suite builds its own clips with ffmpeg rather than reading anything from the
user's video library, so it is reproducible and safe to run anywhere. The
`allp` fixture deliberately mirrors what gpu-screen-recorder produces: H.264
High, all-P (no B-frames), a 2-second GOP and Opus audio in MP4.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

CACHE = os.path.join(tempfile.gettempdir(), f"apricot-tests-{os.getuid()}")
DURATION = 6
FPS = 60
GOP = FPS * 2          # a keyframe every 2s, as the real recordings have

_VIDEO = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
          "-pix_fmt", "yuv420p", "-profile:v", "high", "-g", str(GOP),
          # Fixed GOP with no scene-cut keyframes, so tests can rely on
          # keyframes landing exactly every two seconds.
          "-x264-params", "scenecut=0:open_gop=0",
          "-color_primaries", "bt709", "-color_trc", "bt709",
          "-colorspace", "bt709", "-color_range", "tv"]


# Hard ceiling on any fixture. These clips are a couple of megabytes; anything
# approaching this means an encode has run away, and stopping early beats
# filling the disk.
MAX_FIXTURE_BYTES = 32 * 1024 * 1024


def _run(args: list[str]) -> None:
    # -fs is an output option, so it belongs immediately before the destination,
    # which is always the last argument.
    capped = [*args[:-1], "-fs", str(MAX_FIXTURE_BYTES), args[-1]]
    result = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y", *capped],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"fixture build failed:\n{result.stderr[-800:]}")
    if os.path.getsize(args[-1]) >= MAX_FIXTURE_BYTES:
        raise RuntimeError(f"fixture {args[-1]} hit the size cap; the encode ran away")


def _src(seed: int = 0) -> list[str]:
    return ["-f", "lavfi", "-i",
            f"testsrc2=size=640x360:rate={FPS}:duration={DURATION}"]


def _tone(freq: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={DURATION}"]


@functools.lru_cache(maxsize=None)
def sample(kind: str = "allp") -> str:
    """Path to a cached fixture, building it on first use.

    kind:
      allp      H.264 all-P + Opus, mirroring a screen-recorder capture
      bframes   same but encoded with B-frames, to prove inheritance both ways
      twoaudio  two Opus tracks, as a mic + desktop capture would have
      noaudio   video only
    """
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{kind}.mp4")
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path

    if kind == "allp":
        _run([*_src(), *_tone(440), *_VIDEO, "-bf", "0",
              "-c:a", "libopus", "-b:a", "96k", path])
    elif kind == "bframes":
        _run([*_src(), *_tone(440), *_VIDEO, "-bf", "2",
              "-c:a", "libopus", "-b:a", "96k", path])
    elif kind == "twoaudio":
        _run([*_src(), *_tone(440), *_tone(880), *_VIDEO, "-bf", "0",
              "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
              "-c:a", "libopus", "-b:a", "96k",
              "-metadata:s:a:0", "title=device:default_output",
              "-metadata:s:a:1", "title=Mic", path])
    elif kind == "noaudio":
        _run([*_src(), *_VIDEO, "-bf", "0", "-an", path])
    else:
        raise ValueError(f"unknown fixture {kind!r}")
    return path


@functools.lru_cache(maxsize=None)
def keyframes(path: str) -> tuple[float, ...]:
    """Real keyframe timestamps, as the app itself reads them."""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout
    return tuple(sorted(float(x.strip().rstrip(",")) for x in out.splitlines()
                        if x.strip()))


def probe_json(path: str) -> dict:
    import json
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True).stdout
    return json.loads(out or "{}")


def stream(path: str, kind: str = "video") -> dict:
    for s in probe_json(path).get("streams", []):
        if s.get("codec_type") == kind:
            return s
    return {}


def duration(path: str) -> float:
    """Container duration -- the longest stream, so audio overhang counts."""
    return float(probe_json(path).get("format", {}).get("duration", 0.0))


def stream_duration(path: str, kind: str = "video") -> float:
    """One stream's own duration.

    Worth having separately: a stream copy cannot split an audio packet, so the
    last one overhangs the cut by up to its own length and drags the container
    duration with it. Video is where frame-exactness is actually promised.
    """
    return float(stream(path, kind).get("duration") or 0.0)


def frame_types(path: str, count: int = 30) -> str:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-read_intervals", f"%+{count // FPS + 1}",
         "-show_entries", "frame=pict_type", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout
    return "".join(out.split()).replace(",", "")[:count]


def video_md5(path: str, seek: float | None = None,
              length: float | None = None) -> str:
    """MD5 of the raw video packets -- proves a stream copy was bit-exact."""
    args = [FFMPEG, "-v", "error"]
    if seek is not None:
        args += ["-ss", f"{seek:.6f}"]
    args += ["-i", path]
    if length is not None:
        args += ["-t", f"{length:.6f}"]
    args += ["-map", "0:v", "-c", "copy", "-f", "md5", "-"]
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def cleanup() -> None:
    shutil.rmtree(CACHE, ignore_errors=True)
