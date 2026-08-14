# Apricot Studio

A small video trimmer for Bazzite. Open a file, mark in and out, export the clip.
The export inherits every setting from the source, so there is nothing to configure.

Built for pulling a highlight out of a gpu-screen-recorder replay buffer without
opening kdenlive or DaVinci.

![the window](io.github.raulblideran.ApricotStudio.svg)

## Install

### As a Flatpak

Download `apricot-studio.flatpak` from the
[latest release](https://github.com/raulblideran/apricot-studio/releases), then:

```sh
flatpak install --user apricot-studio.flatpak
```

Nothing else is required — no Python, no PyQt6, no ffmpeg. The bundle carries
its own ffmpeg built with libx264 and libx265, and Qt comes from the KDE
runtime.

The bundle is 16 MB because it holds only the app. It needs
`org.kde.Platform//6.11`, which is about 1.1 GB. If you already run any KDE
Flatpak — kdenlive, Haruna — you have it and the install is instant. Otherwise
Flatpak fetches it once, and every KDE app afterwards shares it.

### Building the Flatpak yourself

```sh
flatpak install --user flathub org.flatpak.Builder org.kde.Sdk//6.11

flatpak run org.flatpak.Builder --user --force-clean --install \
    --repo=repo build-dir io.github.raulblideran.ApricotStudio.yaml
```

That installs it locally. To produce a bundle to give to someone else:

```sh
flatpak build-bundle --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo \
    repo apricot-studio.flatpak io.github.raulblideran.ApricotStudio master
```

**`--runtime-repo` is not optional.** Without it the bundle names the runtime it
needs but not where to find it, so it installs fine for anyone who already has
that runtime and fails for everyone else — which is easy to miss when testing on
a machine that has it. With the flag, Flatpak offers to add Flathub and fetch
the runtime itself.

The first build compiles x264, x265 and ffmpeg, so expect several minutes.
Rebuilds after a code change take seconds: those modules are cached and only the
app module runs again.

### Straight from the source tree

```sh
./install.sh
```

Everything lands under `~/.local`, so nothing touches the rpm-ostree base image
and it survives Bazzite updates. It uses the system `ffmpeg` and the system
PyQt6, which Bazzite already ships — no pip, no venv, no layered packages.
Remove it with `./install.sh --uninstall`.

**Do not run both at once.** They share a desktop ID, and the flatpak's export
directory comes first in `XDG_DATA_DIRS`, so the flatpak wins in the app menu
while `apricot-studio` in a terminal still runs the source tree. Pick one.

## Why the Flatpak is GPL

The KDE runtime's ffmpeg is built without libx264 and libx265, because those are
GPL. H.264 is what screen recorders produce and therefore what this app spends
its time on, so the manifest builds x264, x265 and ffmpeg from source. Linking
them is what makes the bundle **GPL-3.0-or-later**; the alternative was falling
back to `h264_vaapi`, which is measurably worse at the same bitrate and needs a
GPU that can encode H.264 at all.

Three details worth recording, since each cost a build to find:

- x265 sets CMake policies `CMP0025` and `CMP0054` to `OLD`, which CMake 4 — what
  the Sdk ships — refuses outright. The manifest patches them to `NEW`.
- SVT-AV1 3.x changed `svt_av1_enc_init_handle` from three arguments to two, so
  ffmpeg 7.x will not compile against the runtime. The manifest uses ffmpeg 8.0.
- PyQt6 is installed from its wheel with `--no-deps`, deliberately skipping
  `PyQt6-Qt6`. The bindings bundle no Qt of their own, so they bind to the
  runtime's. But Qt then looks for its plugins inside the PyQt6 package, which
  is empty — without `QT_PLUGIN_PATH` pointing at the runtime there is no
  platform plugin and the window never opens.

## Use

```sh
apricot-studio                     # then Open…
apricot-studio ~/Videos/clip.mp4   # or straight to a file
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
- **Move to Trash** is the default. Outside a sandbox it goes through `gio`,
  which picks the right wastebasket for the file's own disk. Inside the Flatpak
  that routes via the Trash portal, which KDE advertises but does not implement,
  so the app writes the trash entry itself instead — same result, still
  restorable from Dolphin.
- **Delete permanently** skips the Trash and asks a second time, on its own
  dialog, because nothing can undo it.
- `Esc` always means keep.

Afterwards the file is closed, dropped from Recent, and the window returns to
its empty state — it will not sit there showing a file that no longer exists.

## Folders the Flatpak cannot see

The Flatpak is allowed into **Videos**, **Pictures** and **Downloads**, and
nowhere else. A file kept anywhere else — a second drive under `/var/mnt`, say —
is not merely off limits: it is not mounted inside the sandbox at all, so
`ffprobe` reports it as *"No such file or directory"*, word for word what it
says about a file that was deleted. That message sent at least one person
looking for a file that was sitting exactly where they left it.

So the app checks before repeating it. If the folder itself is invisible and
this is a Flatpak, the file is almost certainly there and the sandbox is the
problem, and you get a dialog that says so and offers both ways out:

- **Locate the file…** reopens the chooser at that folder. The chooser belongs
  to the desktop portal and runs outside the sandbox, so it can reach what the
  app cannot, and picking the file hands it over. Nothing to restart, but it
  grants that one file — the clip is then written to `~/Videos`, because the
  portal exposes the source in a directory that holds nothing else.
- **Copy command** puts the exact line on your clipboard:

  ```sh
  flatpak override --user --filesystem=/var/mnt/PersonalFiles/Videos \
      io.github.raulblideran.ApricotStudio
  ```

  Run it and start the app again. [Flatseal] does the same thing with a
  checkbox. Either way it is permanent, and exports can then go next to the
  source as usual.

The app cannot grant this itself. Nothing in the portal API allows it, and the
one thing that would — `--talk-name=org.freedesktop.Flatpak` — lets an app run
arbitrary commands on the host, which is far too much to ask for a convenience.
Handing over the right command is as close as this can honestly get.

A file that really is missing, or one `ffprobe` simply refuses, still gets the
plain message it always did.

[Flatseal]: https://flathub.org/apps/com.github.tchx84.Flatseal

## Accent colour

The swatch in the top-right opens eleven accents, each shown as a colour rather
than described. **Apricot** (`#e27125`) is the default; the choice applies
immediately and is remembered.

They span the hue wheel at roughly even lightness, so switching changes the
colour without changing how heavy the interface looks. Every one clears
**4.5:1** against the window background — the accent is used for text, not only
for fills — and the closest pair is ΔE 17 apart, so none of them are hard to
tell from another.

Two colours deliberately ignore the accent, because they are statements rather
than decoration: **green** for "this export is a lossless copy" and **red** for
"this will delete your original". No accent in the palette comes close enough to
either to be mistaken for it, so picking a green accent cannot make the delete
checkbox look reassuring. Text drawn on top of the accent is computed from its
luminance, so a dark colour would stay legible too.

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
as stuttery playback. Apricot Studio reads the source's reorder depth and matches it.

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
| `apricot.py` | window, player, keyboard |
| `media.py` | ffprobe, keyframes, waveform, filmstrip |
| `export.py` | builds and runs the ffmpeg cut |
| `sandbox.py` | what the Flatpak can reach, and what to say when it cannot |
| `timeline.py` | the timeline widget |
| `theme.py` | accent palette and the stylesheet built from it |
| `tests/` | the test suite |

## Tests

```sh
./run-tests.sh           # 315 tests, ~28s (real encodes)
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
| `test_theme.py` | palette contrast, distinctness, and the derived stylesheet |
| `test_sandbox.py` | telling a hidden folder from a missing file, and the command offered for it |
| `test_gui.py` | timeline geometry, zoom, snapping, painting, focus, accent, delete targeting, the blocked-folder dialog |

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
| a hidden folder reported as a missing file | caught |
| the override command left unquoted | caught |
| exporting into the portal's one-file directory | caught |

Two of these were originally *missed* by tests that only looked like they
covered the behaviour — one asserted a value was recorded without checking
anything read it, the other used `endswith` where `"clip..webm"` would have
passed. Both are why the sweep is worth running rather than assumed.

Window tests need a real display and skip cleanly without one, so the suite is
green headless.

## Not included

No filters, scaling, colour correction, transitions or multi-clip editing — that
is what the big editors are for.
