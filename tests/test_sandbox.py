"""Sandbox reasoning tests.

No Qt and no ffmpeg: a written-out /.flatpak-info stands in for the real one,
and a temporary directory stands in for the filesystem the sandbox either does
or does not mount. Both halves of the question -- confined or not -- are forced
explicitly, so the suite gives the same answer whether it is run from a shell
or from inside the flatpak.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import sandbox

# What the app's own /.flatpak-info actually looks like, KDE runtime defaults
# and all. The awkward parts are deliberate: an access mode on two entries, a
# trailing separator, and plumbing nobody wants named in a dialog.
FLATPAK_INFO = """\
[Application]
name=io.github.raulblideran.ApricotStudio

[Instance]
flatpak-version=1.16.1
session-bus-proxy=true

[Context]
shared=ipc;
sockets=fallback-x11;pulseaudio;wayland;
devices=dri;
filesystems=xdg-download;xdg-pictures;xdg-videos;xdg-config/kdeglobals:ro;~/.local/share/Trash:create;

[Session Bus Policy]
org.freedesktop.FileManager1=talk
"""

ROOT_IGNORES_MODES = os.geteuid() == 0


class TempTree(unittest.TestCase):
    """A scratch directory plus the helpers every case here wants."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="apricot-sandbox-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name: str, body: str = "") -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path

    def info_file(self, body: str = FLATPAK_INFO) -> str:
        return self.write("flatpak-info", body)


class ReadingFlatpakInfo(TempTree):
    def test_reports_the_application_id(self):
        self.assertEqual(sandbox.app_id(self.info_file()),
                         "io.github.raulblideran.ApricotStudio")

    def test_lists_granted_filesystems_without_their_access_modes(self):
        self.assertEqual(
            sandbox.granted_folders(self.info_file()),
            ["xdg-download", "xdg-pictures", "xdg-videos",
             "xdg-config/kdeglobals", "~/.local/share/Trash"])

    def test_names_only_folders_a_person_would_recognise(self):
        names = sandbox.reachable_names(self.info_file())
        self.assertEqual(names, ["Downloads", "Pictures", "Videos"])
        # The wastebasket and a KDE config file are plumbing, not places the
        # user put a video in.
        self.assertNotIn("Trash", " ".join(names))

    def test_absent_file_means_no_sandbox(self):
        missing = os.path.join(self.tmp, "nothing-here")
        self.assertIsNone(sandbox.info(missing))
        self.assertFalse(sandbox.is_sandboxed(missing))
        self.assertEqual(sandbox.granted_folders(missing), [])
        self.assertEqual(sandbox.app_id(missing), sandbox.APP_ID)

    def test_a_file_it_cannot_parse_is_not_an_error(self):
        # Confined but unreadable is still confined: the file's presence is the
        # signal, and only the details are lost.
        for junk in ("", "not an ini file at all\n", "[Context\nfilesystems=home"):
            path = self.write("junk-info", junk)
            self.assertIsNone(sandbox.info(path), f"{junk!r} should not parse")
            self.assertTrue(sandbox.is_sandboxed(path))
            self.assertEqual(sandbox.app_id(path), sandbox.APP_ID)
            self.assertEqual(sandbox.reachable_names(path), [])

    def test_values_holding_percent_signs_survive(self):
        # configparser interpolates by default, and would raise on a folder
        # name a person might perfectly well have chosen.
        path = self.write("odd-info",
                          "[Application]\nname=org.example.App\n"
                          "[Context]\nfilesystems=/mnt/100% full;\n")
        self.assertEqual(sandbox.granted_folders(path), ["/mnt/100% full"])


class Diagnosis(TempTree):
    def test_a_file_that_is_right_there_is_not_the_sandbox_s_fault(self):
        # ffprobe still refuses plenty of files it can read perfectly well.
        trouble = sandbox.diagnose(self.write("clip.mp4"), sandboxed=True)
        self.assertEqual(trouble.kind, sandbox.READABLE)

    def test_a_gap_in_a_visible_folder_is_a_missing_file(self):
        gone = os.path.join(self.tmp, "gone.mp4")
        for confined in (True, False):
            trouble = sandbox.diagnose(gone, sandboxed=confined)
            self.assertEqual(trouble.kind, sandbox.MISSING,
                             "the folder is visible, so the file really is gone")

    def test_an_invisible_folder_inside_a_sandbox_is_a_permission_wall(self):
        blocked = os.path.join(self.tmp, "PersonalFiles", "Videos", "MOE.mp4")
        trouble = sandbox.diagnose(blocked, sandboxed=True)
        self.assertEqual(trouble.kind, sandbox.BLOCKED)
        self.assertEqual(trouble.folder, os.path.join(self.tmp, "PersonalFiles", "Videos"),
                         "the folder to allow is the one the file lives in")

    def test_the_same_path_outside_a_sandbox_is_just_missing(self):
        blocked = os.path.join(self.tmp, "PersonalFiles", "Videos", "MOE.mp4")
        self.assertEqual(sandbox.diagnose(blocked, sandboxed=False).kind,
                         sandbox.MISSING,
                         "off flatpak a missing folder means what it says")

    @unittest.skipIf(ROOT_IGNORES_MODES, "root reads regardless of mode bits")
    def test_a_file_it_may_not_read_is_told_apart_from_one_it_cannot_see(self):
        # An override would not help here, so this must not claim it would.
        path = self.write("locked.mp4")
        os.chmod(path, 0o000)
        self.assertEqual(sandbox.diagnose(path, sandboxed=True).kind,
                         sandbox.UNREADABLE)

    def test_it_answers_for_itself_when_not_told_which_world_it_is_in(self):
        gone = os.path.join(self.tmp, "gone.mp4")
        self.assertEqual(sandbox.diagnose(gone).kind,
                         sandbox.diagnose(gone, sandboxed=sandbox.is_sandboxed()).kind)


class TheCommandItHandsOver(TempTree):
    def test_it_names_the_folder_and_this_application(self):
        self.assertEqual(
            sandbox.override_command("/var/mnt/PersonalFiles/Videos", self.info_file()),
            "flatpak override --user --filesystem=/var/mnt/PersonalFiles/Videos "
            "io.github.raulblideran.ApricotStudio")

    def test_it_quotes_folders_a_shell_would_mangle(self):
        command = sandbox.override_command("/mnt/Raul's Drive/My Videos", self.info_file())
        self.assertIn("'/mnt/Raul'\"'\"'s Drive/My Videos'", command)
        self.assertNotIn("--filesystem=/mnt/Raul's Drive", command,
                         "an unquoted path would silently allow the wrong folder")

    def test_it_still_names_the_app_when_flatpak_info_says_nothing(self):
        missing = os.path.join(self.tmp, "nothing-here")
        self.assertTrue(sandbox.override_command("/data", missing)
                        .endswith(sandbox.APP_ID))


class Wording(TempTree):
    def setUp(self):
        super().setUp()
        # The report this came from, rooted somewhere guaranteed not to exist.
        self.folder = os.path.join(self.tmp, "PersonalFiles", "Videos")
        self.blocked = os.path.join(self.folder, "MOE_2489_1.mp4")

    def explain(self):
        trouble = sandbox.diagnose(self.blocked, sandboxed=True)
        self.assertEqual(trouble.kind, sandbox.BLOCKED)
        return sandbox.explain(trouble, self.info_file())

    def test_the_headline_names_the_file(self):
        text, _ = self.explain()
        self.assertIn("MOE_2489_1.mp4", text)

    def test_the_detail_names_the_folder_and_the_way_out(self):
        _, detail = self.explain()
        self.assertIn(self.folder, detail)
        self.assertIn("flatpak override --user", detail)
        self.assertIn("restart Apricot Studio", detail,
                      "an override does nothing until the app is started again")

    def test_it_says_where_it_can_look_instead(self):
        _, detail = self.explain()
        self.assertIn("It can read Downloads, Pictures and Videos", detail)

    def test_it_does_not_repeat_ffprobe_s_lie(self):
        text, detail = self.explain()
        self.assertNotIn("No such file", text + detail,
                         "the file exists; saying otherwise is what caused this")

    def test_it_manages_without_a_list_of_reachable_folders(self):
        missing = os.path.join(self.tmp, "nothing-here")
        trouble = sandbox.diagnose(self.blocked, sandboxed=True)
        _, detail = sandbox.explain(trouble, missing)
        self.assertIn("flatpak override --user", detail)
        self.assertNotIn("It can read", detail)


class WhereTheClipGoes(TempTree):
    def test_an_ordinary_folder_is_kept(self):
        self.assertEqual(sandbox.usable_output_dir(self.tmp), self.tmp)
        source = os.path.join(self.tmp, "clip.mp4")
        self.assertEqual(sandbox.output_dir_for(source), self.tmp,
                         "next to the source is still the useful answer")

    def test_the_document_portal_is_refused(self):
        # Where a file located through the portal lands. The directory exists
        # and looks writable, but only ever holds the one file it was made for.
        doc = os.path.join(self.tmp, "doc", "a1b2c3d4")
        os.makedirs(doc)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp}):
            self.assertIsNone(sandbox.usable_output_dir(doc))
            self.assertEqual(sandbox.output_dir_for(os.path.join(doc, "MOE.mp4")),
                             sandbox.fallback_output_dir())

    @unittest.skipIf(ROOT_IGNORES_MODES, "root writes regardless of mode bits")
    def test_a_folder_it_cannot_write_into_is_refused(self):
        locked = os.path.join(self.tmp, "read-only")
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        self.addCleanup(os.chmod, locked, 0o700)
        self.assertIsNone(sandbox.usable_output_dir(locked))

    def test_a_folder_that_is_not_there_is_refused(self):
        self.assertIsNone(sandbox.usable_output_dir(os.path.join(self.tmp, "nope")))
        self.assertIsNone(sandbox.usable_output_dir(""))

    def test_the_fallback_is_somewhere_that_exists(self):
        self.assertTrue(os.path.isdir(sandbox.fallback_output_dir()))


if __name__ == "__main__":
    unittest.main()
