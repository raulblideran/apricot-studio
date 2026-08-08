# Clipper

A small video trimmer for Bazzite. Open a file, mark in and out, export the clip.
The export inherits every setting from the source, so there is nothing to configure.

Built for pulling a highlight out of a gpu-screen-recorder replay buffer without
opening kdenlive or DaVinci.

![the window](clipper.svg)

## Install

```sh
./install.sh
```

Everything lands under `~/.local`, so nothing touches the rpm-ostree base image
and it survives Bazzite updates. Afterwards Clipper is in the app menu and in
Dolphin's right-click → *Open With*. Remove it with `./install.sh --uninstall`.

There is nothing to download: it uses the system `ffmpeg` and the system PyQt6,
both of which Bazzite already ships. No pip, no venv, no layered packages.

## Use

```sh
clipper                     # then Open…
clipper ~/Videos/clip.mp4   # or straight to a file
```

You can also drop a video onto the window, or pick one from **Recent**.

| | |
|---|---|
| `Space` | play / pause |
| `←` `→` | one frame back / forward |
| `Shift`+`←` `→` | one second |
| `J` `L` | ten seconds |
| `K` | pause |
| `I` `O` | set in / out to the playhead |
| `S` | snap the in-point to the nearest keyframe |
| `Z` | zoom to selection / back out |
| `+` `−` | zoom in / out |
| `Home` `End` | jump to start / end |
| `Ctrl`+`O` | open a file |
| `Ctrl`+`W` | close the current file |
| `Ctrl`+`Enter` | export |
| `Esc` | cancel a running export |

Drag the orange handles to set in and out, drag anywhere else to scrub, or type
an exact timecode into the In/Out boxes. Hovering shows the frame under the
cursor. The wheel zooms, `Shift`+wheel pans, double-click toggles zoom.

The timeline shows a filmstrip, an audio waveform (the spikes are usually the
moment you want) and ticks marking the source's keyframes. **Green ticks are
free cut points** — see below.

**Folder** and **Name** are separate fields. The name is yours: it is suggested
once when you open a file and then left alone, including after a render, so you
can export, tweak the selection and export again without retyping it. If that
would overwrite an existing clip you are asked first. **Show in folder** appears
after an export, and **Close** unloads the file without touching it.

## Instant, lossless cuts

If the in-point lands exactly on a keyframe, nothing has to be decoded: the clip
is stream-copied straight out of the source. Press `S` (or drag the in-handle
near a tick — it snaps, and `Alt` overrides) and the badge turns green.

Measured on a 1440p60 replay: **0.14 s instead of 9.2 s**, with the video stream
coming out **bit-identical** to the source (verified by MD5), and both streams
starting at exactly 0.000. The cost is that the in-point moves to the keyframe,
at most one GOP — 2 seconds on these files. The out-point stays exact either way.

The badge always says which path you are on, so it is never a surprise.

## The few things you can change

Defaults inherit everything, so leaving this row alone reproduces the source.

| Control | What it does |
|---|---|
| **Format** | *Same as source*, **WebM** (VP9 + Opus), or **GIF** (480px, 15fps, with a proper generated palette). |
| **Audio** | Keep, mute, or pick one track when the source has several — a capture with separate desktop and mic tracks lists both. |
| **Quality** | *Same as source*, *Smaller file*, *Smallest worth keeping*, or a size to fit: 10 / 25 / 50 / 100 MB. |

The quality presets scale against **this file's** bitrate rather than some fixed
number, so *Smaller file* means about half of whatever you opened — the same
preset behaves sensibly on a 720p capture and a 4K one. Hover any entry for what
it does. The badge shows the estimated size for your current selection and
updates as you drag; measured against real encodes it lands within 3%.

Only *Same as source* can be a lossless copy — asking for something smaller is,
by definition, asking not to reproduce the original.

Size targets aim safely under, since a limit that gets exceeded is a rejected
upload: a 10 MB target lands around 8.9 MB. WebM is software VP9 and genuinely
slow (no GPU encodes VP9), roughly 5× the time of an H.264 export. GIF has no
size target, since its rate control is a colour palette rather than a bitrate.

## Deleting the original

**Delete original after export** offers to remove the file you cut from, which
is the point of trimming a 110 MB replay buffer down to the ten seconds worth
keeping. It never acts on its own:

- It only ever asks **after a successful export**, and only if the clip it wrote
  is a real file of non-trivial size. A zero-byte export leaves the original
  alone and says so.
- The prompt names the file, its size, and **how much of it you are throwing
  away** — "your clip kept 14.0s, about 15%; the other 85% is only in this file".
- **Move to Trash** is the default, and goes through `gio` so the file lands in
  the desktop wastebasket on its own filesystem and stays recoverable from
  Dolphin.
- **Delete permanently** skips the Trash and asks a second time, on its own
  dialog, because nothing can undo it.
- `Esc` always means keep.

Afterwards the file is closed, dropped from Recent, and the window returns to
its empty state — it will not sit there showing a file that no longer exists.

## What "inherits the settings" means

`ffprobe` reads the source once and every encoding decision follows from it:

| | |
|---|---|
| Container | same as the source |
| Resolution, frame rate | untouched, VFR timestamps preserved |
| Codec | `h264`→libx264, `hevc`→libx265, `av1`→av1_vaapi, `vp9`→libvpx-vp9 |
| Profile, pixel format, colour | copied from the source |
| Keyframe interval | measured from the source |
| **GOP structure** | **B-frame count matched to the source** |
| Bitrate | capped CRF against the source's own bitrate |
| **Audio** | **stream-copied — bit identical, never re-encoded** |

That GOP-structure row matters more than it looks. Screen recorders encode
all-P (`IPPPP…`, no B-frames) for low latency, but an encoder left to itself
adds them — which forces the decoder to buffer and reorder frames, and shows up
as stuttery playback. Clipper reads the source's reorder depth and matches it.

Cuts are frame-exact at both ends. Because that requires re-encoding the video,
each export costs one generation; measured against a 1440p60 9.5 Mb/s replay the
result is 37.4 dB PSNR at 1.00× the source bitrate, which is not visible. The
re-encoded clip decodes slightly *cheaper* than the source it came from.

Audio is copied rather than re-encoded, so it takes no loss at all. It can only
start on a whole packet, which leaves it within one 20 ms Opus frame of the
video — measured at 17 ms, inaudible.

Encoding runs at roughly 1.5× real time for 1440p60 on a Ryzen 7 5700X3D, so a
15-second clip takes about 10 seconds. AV1 sources go to the GPU encoder instead,
since AV1 in software is far too slow at 4K.

## Files

| | |
|---|---|
| `clipper.py` | window, player, keyboard |
| `media.py` | ffprobe, keyframes, waveform, filmstrip |
| `export.py` | builds and runs the ffmpeg cut |
| `timeline.py` | the timeline widget |
| `tests/` | the test suite |

## Tests

```sh
./run-tests.sh           # 234 tests, ~24s (real encodes)
./run-tests.sh --fast    # ~4s, skips anything that invokes ffmpeg
./run-tests.sh -v        # one line per test
```

Uses `unittest` and `PyQt6.QtTest` — both already on the system, so there is
nothing to install and the suite runs unchanged inside a flatpak. `pytest` has
nicer ergonomics but isn't in the Bazzite base image, and adding it would make
it this project's only dependency.

| Module | Covers |
|---|---|
| `test_units.py` | timecode, output naming, keyframe matching, size arithmetic, track labels |
| `test_commands.py` | the ffmpeg arguments produced for every combination of options |
| `test_edges.py` | malformed input, hostile filenames, degenerate ranges, unreadable sources |
| `test_encode.py` | real exports: bit-identity, durations, A/V sync, GOP structure, formats |
| `test_gui.py` | timeline geometry, zoom, snapping, painting, focus, delete targeting |

Fixtures are synthesised with ffmpeg into `/tmp` — the suite never reads your
video library. The `allp` fixture deliberately mirrors a gpu-screen-recorder
capture (H.264 all-P, 2 s GOP, Opus in MP4).

**The tests are checked against the bugs they exist for.** Every defect this
project shipped was silent — the clip played fine, it was just wrong — so each
fix has a test, and each of those tests has been confirmed to fail when the fix
is reverted:

| Reverted fix | Result |
|---|---|
| keyframe rounding | caught |
| size-target overshoot | caught |
| B-frames the source never had | caught |
| single-seek A/V desync | caught |
| dropping extra audio tracks | caught |
| extension read from the whole path | caught |
| timecode accepting `nan`/`inf`/negative | caught |
| formatter crashing on non-finite input | caught |
| negative seek reaching ffmpeg | caught |
| size-target bitrate left unbounded | caught |
| output losing its extension | caught |
| delete prompt following the open file | caught |

Two of these were originally *missed* by tests that only looked like they
covered the behaviour — one asserted a value was recorded without checking
anything read it, the other used `endswith` where `"clip..webm"` would have
passed. Both are why the sweep is worth running rather than assumed.

Window tests need a real display and skip cleanly without one, so the suite is
green headless.

## Not included

No filters, scaling, colour correction, transitions or multi-clip editing — that
is what the big editors are for.
