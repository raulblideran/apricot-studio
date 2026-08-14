# Apricot Studio -- a small video trimmer.
# Copyright (C) 2026 Raul Blideran
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file for the full text.
"""What the Flatpak sandbox can and cannot reach.

Inside the Flatpak only a few locations are mounted, so a video on a second
drive does not merely fail to open -- it does not appear to exist at all, and
ffprobe says so in exactly the words it would use for a deleted file. That is
the one failure the user can fix, and the one they cannot diagnose, so this
module works out which case it is and what to tell them.

Nothing here imports Qt, and outside a Flatpak every function answers "there is
no sandbox", so the rest of the app can call it unconditionally.
"""

from __future__ import annotations

import configparser
import os
import shlex
from dataclasses import dataclass

FLATPAK_INFO = "/.flatpak-info"

# Only used when /.flatpak-info cannot say: the command the dialog offers still
# has to name the right application.
APP_ID = "io.github.raulblideran.ApricotStudio"

# What diagnose() concluded about a file that would not open.
READABLE = "readable"      # it is right there -- ffprobe failed for its own reasons
MISSING = "missing"        # the folder is visible and the file is not in it
UNREADABLE = "unreadable"  # it is there but its permissions say no
BLOCKED = "blocked"        # the sandbox is not mounting the folder it lives in

# A filesystem entry may carry the access it was granted with.
_ACCESS_MODES = (":ro", ":rw", ":create")

# Manifest names for places a person would recognise. Everything else a
# manifest lists is plumbing -- the wastebasket, a config file -- and saying it
# out loud in a dialog about videos would only confuse.
_FRIENDLY = {
    "home": "Home",
    "host": "the whole system",
    "xdg-desktop": "Desktop",
    "xdg-documents": "Documents",
    "xdg-download": "Downloads",
    "xdg-music": "Music",
    "xdg-pictures": "Pictures",
    "xdg-public-share": "Public",
    "xdg-templates": "Templates",
    "xdg-videos": "Videos",
}


@dataclass(frozen=True)
class Trouble:
    """Why a file would not open, and the folder to talk about."""

    kind: str
    path: str
    folder: str


def info(source: str = FLATPAK_INFO) -> configparser.ConfigParser | None:
    """/.flatpak-info, parsed, or None if there is nothing to parse.

    Never raises. A missing file and a malformed one mean the same thing to
    every caller: no reliable answer about what this sandbox allows.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with open(source, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeDecodeError, configparser.Error):
        return None
    # An empty file parses cleanly and says nothing, which is the same answer.
    return parser if parser.sections() else None


def is_sandboxed(source: str = FLATPAK_INFO) -> bool:
    """Whether this process is running inside a Flatpak.

    The file's presence is the signal, not its contents -- a corrupt one still
    means the app is confined.
    """
    return os.path.exists(source)


def app_id(source: str = FLATPAK_INFO) -> str:
    parsed = info(source)
    if parsed is None:
        return APP_ID
    return parsed.get("Application", "name", fallback="").strip() or APP_ID


def granted_folders(source: str = FLATPAK_INFO) -> list[str]:
    """The locations this sandbox was given, as the manifest words them.

    For telling the user what is already allowed, nothing more. Whether a path
    is reachable is settled by looking at the filesystem: names like
    `xdg-videos` and `host` do not resolve to a directory without knowing the
    user's own layout, and a wrong guess here would misreport the one thing
    this module exists to get right.
    """
    parsed = info(source)
    if parsed is None:
        return []
    folders = []
    for entry in parsed.get("Context", "filesystems", fallback="").split(";"):
        entry = entry.strip()
        for mode in _ACCESS_MODES:
            if entry.endswith(mode):
                entry = entry[: -len(mode)]
                break
        if entry and entry not in folders:
            folders.append(entry)
    return folders


def reachable_names(source: str = FLATPAK_INFO) -> list[str]:
    """The granted folders a person would recognise, in reading order."""
    names = []
    for entry in granted_folders(source):
        label = _FRIENDLY.get(entry)
        if label and label not in names:
            names.append(label)
    return names


def diagnose(path: str, *, sandboxed: bool | None = None) -> Trouble:
    """Why `path` would not open, in terms the user can act on.

    The test is the filesystem itself rather than the granted list, because
    inside the sandbox an ungranted location is simply not mounted: a folder
    that does not exist here is precisely the folder that was never allowed.
    Outside a sandbox that same missing folder means what it says, so this
    stays quiet and lets the ordinary message through.
    """
    folder = os.path.dirname(path) or "."
    if os.path.exists(path):
        return Trouble(READABLE if os.access(path, os.R_OK) else UNREADABLE,
                       path, folder)
    if os.path.isdir(folder):
        # We can see where it belongs, so it really is gone.
        return Trouble(MISSING, path, folder)
    if sandboxed is None:
        sandboxed = is_sandboxed()
    return Trouble(BLOCKED if sandboxed else MISSING, path, folder)


def override_command(folder: str, source: str = FLATPAK_INFO) -> str:
    """The host command that allows `folder` from now on.

    Quoted, because this is meant to be pasted into a shell and plenty of
    folders have spaces in them.
    """
    return ("flatpak override --user "
            f"--filesystem={shlex.quote(folder)} {app_id(source)}")


def explain(trouble: Trouble, source: str = FLATPAK_INFO) -> tuple[str, str]:
    """Headline and detail for a folder the sandbox is hiding.

    The wording lives here rather than in the dialog so it can be tested
    without putting a window on screen.
    """
    reachable = reachable_names(source)
    if len(reachable) > 1:
        allowed = f" It can read {', '.join(reachable[:-1])} and {reachable[-1]}."
    elif reachable:
        allowed = f" It can read {reachable[0]}."
    else:
        allowed = ""

    text = f"Apricot Studio is not allowed to open “{os.path.basename(trouble.path)}”."
    detail = (
        f"{trouble.folder} is not one of the folders this Flatpak may read. "
        f"The file is hidden, not missing.{allowed}"
        "\n\nLocate the file to open it once. To allow the folder permanently, "
        "run this and restart Apricot Studio:\n\n"
        f"{override_command(trouble.folder, source)}"
    )
    return text, detail


def document_portal_root() -> str:
    """Where the file chooser portal hands over a file it has just granted."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "doc")


def usable_output_dir(folder: str) -> str | None:
    """`folder` if a clip can be written into it, else None.

    Two ways it cannot be. A file located through the portal is exposed in a
    one-file directory under the document portal, where siblings cannot be
    created; and a source may simply sit somewhere this app cannot write.
    """
    if not folder:
        return None
    folder = os.path.abspath(folder)
    root = document_portal_root()
    if folder == root or folder.startswith(root + os.sep):
        return None
    if not os.path.isdir(folder) or not os.access(folder, os.W_OK | os.X_OK):
        return None
    return folder


def fallback_output_dir() -> str:
    """Where the clip goes when the source's own folder will not take it."""
    videos = os.path.expanduser("~/Videos")
    return videos if os.path.isdir(videos) else os.path.expanduser("~")


def output_dir_for(source: str) -> str:
    """The folder to suggest for the clip cut out of `source`."""
    return usable_output_dir(os.path.dirname(source)) or fallback_output_dir()
