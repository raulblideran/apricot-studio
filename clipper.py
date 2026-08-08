#!/usr/bin/env python3
"""Clipper -- open a video, mark in and out, export the clip.

The export inherits every setting from the source file, so there is nothing to
configure. Run with an optional file path:

    python3 clipper.py ~/Videos/Replay_2026-02-23_22-28-36.mp4
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QSettings, Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                             QMenu, QMessageBox, QProgressBar, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

import export
from media import (KeyframeLoader, MediaInfo, ThumbnailLoader, WaveformLoader,
                   fmt_tc, parse_tc, probe)
from timeline import Timeline

VIDEO_SUFFIXES = "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.ts *.flv *.wmv *.mpg *.mpeg"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".flv",
              ".wmv", ".mpg", ".mpeg", ".m2ts", ".3gp", ".ogv"}
MAX_RECENT = 10

# Below this an "export" cannot be a real clip, and the original must be kept.
MIN_CLIP_BYTES = 1024

# Common upload ceilings. The value is bytes; 0 means inherit the source.
SIZE_CHOICES = [("Match source", 0), ("10 MB  ·  Discord", 10_000_000),
                ("25 MB", 25_000_000), ("50 MB  ·  Nitro Basic", 50_000_000),
                ("100 MB", 100_000_000)]

STYLE = """
QMainWindow, QWidget { background: #1c1e22; color: #e6e8ec; }
QLabel { color: #e6e8ec; }
QLabel#meta { color: #9aa2b1; }
QLabel#status { color: #9aa2b1; }
QLabel#title { font-size: 13px; font-weight: 600; }
QLabel#duration { color: #ffb02e; font-weight: 600; }
QPushButton {
    background: #2a2e35; border: 1px solid #383d47; border-radius: 5px;
    padding: 5px 11px; color: #e6e8ec;
}
QPushButton:hover { background: #333842; }
QPushButton:pressed { background: #23262c; }
QPushButton:disabled { color: #6a7181; background: #24272d; }
QPushButton#primary {
    background: #ffb02e; border: 1px solid #ffb02e; color: #241a06; font-weight: 600;
    padding: 7px 22px;
}
QPushButton#primary:hover { background: #ffc154; }
QPushButton#primary:disabled { background: #4a4028; border-color: #4a4028; color: #8d8467; }
QPushButton#step { padding: 4px 7px; font-family: monospace; }
QLineEdit {
    background: #14161a; border: 1px solid #383d47; border-radius: 5px;
    padding: 5px 8px; color: #e6e8ec; selection-background-color: #ffb02e;
    selection-color: #241a06;
}
QLineEdit:focus { border-color: #ffb02e; }
QLineEdit#tc { font-family: monospace; max-width: 130px; }
QComboBox {
    background: #2a2e35; border: 1px solid #383d47; border-radius: 5px;
    padding: 4px 8px; color: #e6e8ec; min-width: 96px;
}
QComboBox:hover { background: #333842; }
QComboBox:disabled { color: #6a7181; background: #24272d; }
QComboBox::drop-down { border: none; width: 16px; }
QComboBox QAbstractItemView {
    background: #22252b; border: 1px solid #383d47; color: #e6e8ec;
    selection-background-color: #ffb02e; selection-color: #241a06; outline: none;
}
QMenu { background: #22252b; border: 1px solid #383d47; color: #e6e8ec; }
QMenu::item:selected { background: #ffb02e; color: #241a06; }
QLabel#badge { color: #ffb02e; font-weight: 600; }
QLabel#badgeFast { color: #6ee7a0; font-weight: 600; }
QCheckBox { color: #9aa2b1; spacing: 6px; }
QCheckBox:disabled { color: #5a616f; }
/* Armed, this destroys a file. It should not look like the other options. */
QCheckBox:checked { color: #ff8080; font-weight: 600; }
QCheckBox::indicator { width: 13px; height: 13px; border-radius: 3px;
                       border: 1px solid #4a505d; background: #14161a; }
QCheckBox::indicator:checked { background: #e05252; border-color: #e05252; }
QProgressBar {
    background: #14161a; border: 1px solid #383d47; border-radius: 5px;
    height: 8px; text-align: center; color: transparent;
}
QProgressBar::chunk { background: #ffb02e; border-radius: 4px; }
QFrame#sep { background: #2a2e35; max-height: 1px; border: none; }
"""


@dataclass(frozen=True)
class Exported:
    """What an export was actually made from, captured when it started.

    Nothing about a finished export may be read from the currently-open file.
    Opening another video mid-encode is easy and reasonable, and if the delete
    prompt consulted the live selection it would offer to destroy whatever
    happens to be loaded when the encode lands, rather than the file that was
    cut.
    """

    source: str
    output: str
    duration: float
    kept: float


class Clipper(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        self.setWindowTitle("Clipper")
        self.resize(1180, 900)

        self._info: MediaInfo | None = None
        self._keyframes: list[float] = []
        self._audio_ready = False
        self._pending = path
        self._stop_at_out = False
        self._was_playing = False
        self._last_output: str | None = None
        self._exported: Exported | None = None
        self._settings = QSettings("Clipper", "Clipper")

        self.setAcceptDrops(True)

        self._player = QMediaPlayer(self)
        self._video = QVideoWidget()
        self._video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._video.setMinimumHeight(280)
        # Clicking the picture takes focus off whatever text field had it.
        self._video.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._player.setVideoOutput(self._video)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_playstate)
        self._player.errorOccurred.connect(self._on_player_error)

        self._keyframe_loader = KeyframeLoader(self)
        self._waveform_loader = WaveformLoader(self)
        self._thumb_loader = ThumbnailLoader(self)
        self._keyframe_loader.ready.connect(self._on_keyframes)
        self._waveform_loader.ready.connect(self._timeline_peaks)
        self._thumb_loader.frame.connect(self._timeline_thumb)

        self._exporter = export.Exporter(self)
        self._exporter.progress.connect(self._on_export_progress)
        self._exporter.finished.connect(self._on_export_finished)

        self._build_ui()
        self.setStyleSheet(STYLE)
        self._set_loaded(False)
        QApplication.instance().installEventFilter(self)

    # ----- construction -------------------------------------------------

    def _button(self, text, slot, tooltip="", name="") -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep Space for play/pause
        if tooltip:
            b.setToolTip(tooltip)
        if name:
            b.setObjectName(name)
        return b

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setObjectName("sep")
        line.setFrameShape(QFrame.Shape.HLine)
        return line

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(9)

        # Header ---------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(6)
        self._open_btn = self._button("Open…", self.open_file, "Ctrl+O")
        self._recent_btn = self._button("Recent ▾", self._show_recent,
                                        "Files opened previously")
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self._title = QLabel("No file loaded")
        self._title.setObjectName("title")
        self._meta = QLabel("Open a video to begin")
        self._meta.setObjectName("meta")
        titles.addWidget(self._title)
        titles.addWidget(self._meta)
        header.addWidget(self._open_btn)
        header.addWidget(self._recent_btn)
        header.addSpacing(6)
        header.addLayout(titles, 1)
        outer.addLayout(header)

        outer.addWidget(self._video, 1)

        # Transport ------------------------------------------------------
        transport = QHBoxLayout()
        transport.setSpacing(8)
        self._play_btn = self._button("▶  Play", self.toggle_play, "Space")
        self._clock = QLabel("00:00:00.000 / 00:00:00.000")
        self._clock.setStyleSheet("font-family: monospace;")
        transport.addWidget(self._play_btn)
        transport.addWidget(self._clock)
        transport.addStretch(1)
        transport.addWidget(self._button("⏮  Start", lambda: self._seek(0), "Home"))
        transport.addWidget(self._button("◀◀  10s", lambda: self._nudge(-10.0), "J"))
        transport.addWidget(self._button("◀  1f", lambda: self._step(-1), "Left arrow", "step"))
        transport.addWidget(self._button("1f  ▶", lambda: self._step(1), "Right arrow", "step"))
        transport.addWidget(self._button("10s  ▶▶", lambda: self._nudge(10.0), "L"))
        outer.addLayout(transport)

        # Timeline -------------------------------------------------------
        self._timeline = Timeline()
        self._timeline.seeked.connect(self._seek)
        self._timeline.scrubbing.connect(self._on_scrub)
        self._timeline.inChanged.connect(self._on_in_dragged)
        self._timeline.outChanged.connect(self._on_out_dragged)
        outer.addWidget(self._timeline)

        outer.addWidget(self._separator())

        # In / out -------------------------------------------------------
        self._in_edit = QLineEdit()
        self._out_edit = QLineEdit()
        for edit in (self._in_edit, self._out_edit):
            edit.setObjectName("tc")
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._in_edit.editingFinished.connect(self._on_in_typed)
        self._out_edit.editingFinished.connect(self._on_out_typed)
        # Enter commits the value and hands the keyboard back to the player.
        for edit in (self._in_edit, self._out_edit):
            edit.returnPressed.connect(edit.clearFocus)

        marks = QHBoxLayout()
        marks.setSpacing(6)
        marks.addWidget(QLabel("In"))
        marks.addWidget(self._in_edit)
        marks.addWidget(self._button("◀", lambda: self._bump_in(-1), "Back one frame", "step"))
        marks.addWidget(self._button("▶", lambda: self._bump_in(1), "Forward one frame", "step"))
        marks.addWidget(self._button("Set to playhead  (I)", self.set_in))
        marks.addWidget(self._button(
            "Snap", self.snap_in_to_keyframe,
            "Snap the in-point to the nearest keyframe (S)\n"
            "The export then becomes an instant, bit-perfect copy"))
        marks.addSpacing(18)
        marks.addWidget(QLabel("Out"))
        marks.addWidget(self._out_edit)
        marks.addWidget(self._button("◀", lambda: self._bump_out(-1), "Back one frame", "step"))
        marks.addWidget(self._button("▶", lambda: self._bump_out(1), "Forward one frame", "step"))
        marks.addWidget(self._button("Set to playhead  (O)", self.set_out))
        marks.addStretch(1)
        self._sel_label = QLabel("—")
        self._sel_label.setObjectName("duration")
        marks.addWidget(self._sel_label)
        marks.addWidget(self._button("Play selection", self.play_selection))
        outer.addLayout(marks)

        outer.addWidget(self._separator())

        # Output ---------------------------------------------------------
        out_row = QHBoxLayout()
        out_row.setSpacing(6)
        out_row.addWidget(QLabel("Save to"))
        self._out_path = QLineEdit()
        self._out_path.returnPressed.connect(self._out_path.clearFocus)
        out_row.addWidget(self._out_path, 1)
        out_row.addWidget(self._button("…", self.choose_output, "Choose where to save"))
        outer.addLayout(out_row)

        # Anything that can deviate from the source lives here. Defaults inherit
        # everything, so leaving this row alone reproduces the source exactly.
        opts = QHBoxLayout()
        opts.setSpacing(6)
        self._fmt_box = QComboBox()
        for text, value in (("Same as source", export.SOURCE),
                            ("WebM  ·  VP9", export.WEBM), ("GIF", export.GIF)):
            self._fmt_box.addItem(text, value)
        self._fmt_box.currentIndexChanged.connect(self._on_format_changed)

        self._audio_box = QComboBox()
        self._audio_box.currentIndexChanged.connect(self._on_options_changed)

        self._size_box = QComboBox()
        for text, value in SIZE_CHOICES:
            self._size_box.addItem(text, value)
        self._size_box.currentIndexChanged.connect(self._on_options_changed)

        # Like the buttons, these never take focus, so Space stays play/pause
        # instead of popping a dropdown open.
        for box in (self._fmt_box, self._audio_box, self._size_box):
            box.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._audio_label = QLabel("Audio")
        opts.addWidget(QLabel("Format"))
        opts.addWidget(self._fmt_box)
        opts.addSpacing(12)
        opts.addWidget(self._audio_label)
        opts.addWidget(self._audio_box)
        opts.addSpacing(12)
        opts.addWidget(QLabel("Size"))
        opts.addWidget(self._size_box)
        opts.addSpacing(18)
        self._delete_source = QCheckBox("Delete original after export")
        self._delete_source.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._delete_source.setToolTip(
            "After a successful export, offer to remove the file you cut from.\n"
            "You are always asked first, and Trash is the default.")
        opts.addWidget(self._delete_source)
        opts.addStretch(1)
        self._badge = QLabel("")
        self._badge.setObjectName("badge")
        opts.addWidget(self._badge)
        outer.addLayout(opts)

        # Export ---------------------------------------------------------
        export_row = QHBoxLayout()
        export_row.setSpacing(10)
        self._status = QLabel("")
        self._status.setObjectName("status")
        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setVisible(False)
        self._cancel_btn = self._button("Cancel", self._exporter.cancel)
        self._cancel_btn.setVisible(False)
        self._reveal_btn = self._button("Show in folder", self._reveal)
        self._reveal_btn.setVisible(False)
        self._export_btn = self._button("Export clip", self.start_export, "Ctrl+Return", "primary")
        export_row.addWidget(self._status, 1)
        export_row.addWidget(self._progress, 1)
        export_row.addWidget(self._reveal_btn)
        export_row.addWidget(self._cancel_btn)
        export_row.addWidget(self._export_btn)
        outer.addLayout(export_row)

        self.setCentralWidget(root)

    def showEvent(self, event):
        super().showEvent(event)
        # Audio device enumeration can block; keep it off the startup path.
        if not self._audio_ready:
            self._audio_ready = True
            QTimer.singleShot(0, self._start_audio)

    def _start_audio(self) -> None:
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.8)
        self._player.setAudioOutput(self._audio)
        if self._pending:
            path, self._pending = self._pending, None
            self.load(path)

    # ----- loading ------------------------------------------------------

    def _set_loaded(self, loaded: bool) -> None:
        for widget in (self._play_btn, self._export_btn, self._in_edit,
                       self._out_edit, self._out_path, self._fmt_box,
                       self._audio_box, self._size_box, self._delete_source):
            widget.setEnabled(loaded)

    # ----- recent files -------------------------------------------------

    def _recent(self) -> list[str]:
        stored = self._settings.value("recent", [], type=list) or []
        return [p for p in stored if isinstance(p, str) and os.path.exists(p)]

    def _push_recent(self, path: str) -> None:
        items = [p for p in self._recent() if p != path]
        self._settings.setValue("recent", [path] + items[:MAX_RECENT - 1])

    def _show_recent(self) -> None:
        menu = QMenu(self)
        items = self._recent()
        if not items:
            menu.addAction("No recent files").setEnabled(False)
        for path in items:
            action = menu.addAction(os.path.basename(path))
            action.setToolTip(path)
            action.triggered.connect(lambda _=False, p=path: self.load(p))
        if items:
            menu.addSeparator()
            menu.addAction("Clear list").triggered.connect(
                lambda: self._settings.setValue("recent", []))
        menu.exec(self._recent_btn.mapToGlobal(
            self._recent_btn.rect().bottomLeft()))

    # ----- drag and drop ------------------------------------------------

    @staticmethod
    def _dropped_video(event) -> str | None:
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.splitext(path)[1].lower() in VIDEO_EXTS:
                return path
        return None

    def dragEnterEvent(self, event):
        if self._dropped_video(event):
            event.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event):
        path = self._dropped_video(event)
        if path:
            event.acceptProposedAction()
            self.load(path)

    def open_file(self) -> None:
        start = os.path.dirname(self._info.path) if self._info else os.path.expanduser("~/Videos")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", start, f"Video files ({VIDEO_SUFFIXES});;All files (*)")
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        path = os.path.abspath(os.path.expanduser(path))
        try:
            info = probe(path)
        except (RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot open", f"{os.path.basename(path)}\n\n{exc}")
            return

        self._info = info
        self._keyframes = []
        self.setWindowTitle(f"{os.path.basename(path)} — Clipper")
        self._title.setText(os.path.basename(path))
        self._meta.setText(info.summary())

        self._timeline.reset(info.duration)
        self._keyframe_loader.load(path)
        self._waveform_loader.load(info)
        self._thumb_loader.load(path)

        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.pause()
        self._set_loaded(True)
        self._rebuild_audio_box(info)
        self._out_path.setText(export.default_output(path, self._options().ext_for(info)))
        self._reveal_btn.setVisible(False)
        self._sync_marks()
        self._update_clock(0)
        self._status.setText("")
        self._push_recent(path)

    # ----- export options -----------------------------------------------

    def _rebuild_audio_box(self, info: MediaInfo) -> None:
        """Only offer what the file actually has."""
        box = self._audio_box
        box.blockSignals(True)
        box.clear()
        if not info.has_audio:
            box.addItem("No audio in source", export.NO_AUDIO)
            box.setEnabled(False)
            self._audio_label.setEnabled(False)
        else:
            self._audio_label.setEnabled(True)
            box.setEnabled(True)
            if len(info.audio) > 1:
                box.addItem(f"All {len(info.audio)} tracks", export.ALL_AUDIO)
                for track in info.audio:
                    box.addItem(track.label(), track.index)
            else:
                box.addItem("Keep audio", export.ALL_AUDIO)
            box.addItem("Mute  ·  no audio", export.NO_AUDIO)
        box.blockSignals(False)

    def _options(self) -> export.Options:
        fmt = self._fmt_box.currentData() or export.SOURCE
        audio = self._audio_box.currentData()
        if audio is None:
            audio = export.ALL_AUDIO
        size = self._size_box.currentData() or 0
        # A size target is meaningless for GIF, whose rate control is a palette.
        return export.Options(fmt=fmt, audio=audio,
                              target_bytes=0 if fmt == export.GIF else size)

    def _on_format_changed(self) -> None:
        if not self._info:
            return
        # Keep the destination's extension honest about what will be written.
        current = self._out_path.text().strip()
        want = self._options().ext_for(self._info)
        if current:
            self._out_path.setText(os.path.splitext(current)[0] + "." + want)
        self._on_options_changed()

    def _on_options_changed(self) -> None:
        gif = self._fmt_box.currentData() == export.GIF
        self._size_box.setEnabled(bool(self._info) and not gif)
        self._audio_box.setEnabled(bool(self._info) and self._info.has_audio and not gif)
        self._update_badge()

    def _update_badge(self) -> None:
        """Say plainly which of the two very different paths an export will take."""
        if not self._info:
            self._badge.setText("")
            return
        options = self._options()
        start = self._timeline.in_point
        if (options.fmt == export.SOURCE and not options.target_bytes
                and export.on_keyframe(start, self._keyframes) is not None):
            # Colour carries the distinction; glyphs like a lightning bolt do not
            # render in this font and came out blank.
            self._badge.setObjectName("badgeFast")
            self._badge.setText("Lossless copy  ·  instant, bit-perfect")
        else:
            self._badge.setObjectName("badge")
            label = export.encoder_label(self._info, options)
            detail = ("matched to source" if not options.target_bytes
                      else f"{options.target_bytes // 1_000_000} MB target")
            self._badge.setText(f"Re-encode  ·  {detail}  ·  {label}")
        # An objectName change needs a re-polish before the new rule applies.
        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)

    @pyqtSlot(list)
    def _on_keyframes(self, times: list[float]) -> None:
        self._keyframes = times
        self._timeline.set_keyframes(times)
        self._update_badge()   # only now can we tell whether a copy is possible

    @pyqtSlot(list)
    def _timeline_peaks(self, peaks: list[float]) -> None:
        self._timeline.set_peaks(peaks)

    def _timeline_thumb(self, image) -> None:
        self._timeline.add_thumbnail(image)

    def _on_player_error(self, error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self._status.setText(f"Preview error: {message}")

    # ----- playback -----------------------------------------------------

    def toggle_play(self) -> None:
        if not self._info:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._stop_at_out = False
            if self._player.position() >= self._player.duration() - 20:
                self._player.setPosition(0)
            self._player.play()

    def play_selection(self) -> None:
        if not self._info:
            return
        self._player.setPosition(int(self._timeline.in_point * 1000))
        self._stop_at_out = True
        self._player.play()

    def _on_playstate(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("❚❚  Pause" if playing else "▶  Play")

    def _seek(self, seconds: float) -> None:
        if not self._info:
            return
        self._stop_at_out = False
        target = min(max(seconds, 0.0), self._info.duration)
        self._player.setPosition(int(round(target * 1000)))
        self._timeline.set_position(target)
        self._update_clock(target)

    def _nudge(self, delta: float) -> None:
        if self._info:
            self._seek(self._player.position() / 1000.0 + delta)

    def _step(self, frames: int) -> None:
        if not self._info:
            return
        self._player.pause()
        self._seek(self._player.position() / 1000.0 + frames * self._info.frame_duration)

    def _on_scrub(self, active: bool) -> None:
        # Pause while dragging the playhead, then resume if we were playing.
        if active:
            self._was_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self._player.pause()
        elif self._was_playing:
            self._was_playing = False
            self._player.play()

    def _on_position(self, ms: int) -> None:
        seconds = ms / 1000.0
        if self._stop_at_out and seconds >= self._timeline.out_point:
            self._player.pause()
            self._stop_at_out = False
        self._timeline.set_position(seconds)
        self._update_clock(seconds)

    def _update_clock(self, seconds: float) -> None:
        total = self._info.duration if self._info else 0.0
        self._clock.setText(f"{fmt_tc(seconds)} / {fmt_tc(total)}")

    # ----- in / out -----------------------------------------------------

    def set_in(self) -> None:
        if self._info:
            self._timeline.set_in(min(self._player.position() / 1000.0,
                                      self._timeline.out_point))
            self._sync_marks()

    def set_out(self) -> None:
        if self._info:
            self._timeline.set_out(max(self._player.position() / 1000.0,
                                       self._timeline.in_point))
            self._sync_marks()

    def snap_in_to_keyframe(self) -> None:
        """Move the in-point onto the nearest keyframe, whatever the distance.

        Hitting one by dragging is fiddly, and it is the difference between a
        ten-second encode and an instant copy.
        """
        if not self._info or not self._keyframes:
            return
        target = export.snap_to_keyframe(self._timeline.in_point,
                                         self._keyframes, self._info.duration)
        self._timeline.set_in(min(target, self._timeline.out_point))
        self._sync_marks()

    def _bump_in(self, frames: int) -> None:
        if self._info:
            self._timeline.set_in(self._timeline.in_point + frames * self._info.frame_duration)
            self._sync_marks()

    def _bump_out(self, frames: int) -> None:
        if self._info:
            self._timeline.set_out(self._timeline.out_point + frames * self._info.frame_duration)
            self._sync_marks()

    def _on_in_dragged(self, value: float) -> None:
        self._sync_marks()

    def _on_out_dragged(self, value: float) -> None:
        self._sync_marks()

    def _on_in_typed(self) -> None:
        value = parse_tc(self._in_edit.text())
        if value is not None and self._info:
            self._timeline.set_in(min(value, self._timeline.out_point))
        self._sync_marks()

    def _on_out_typed(self) -> None:
        value = parse_tc(self._out_edit.text())
        if value is not None and self._info:
            self._timeline.set_out(max(value, self._timeline.in_point))
        self._sync_marks()

    def _sync_marks(self) -> None:
        start, end = self._timeline.in_point, self._timeline.out_point
        self._in_edit.setText(fmt_tc(start))
        self._out_edit.setText(fmt_tc(end))
        self._sel_label.setText(f"Selection  {end - start:.3f}s")
        self._update_badge()

    # ----- export -------------------------------------------------------

    def choose_output(self) -> None:
        if not self._info:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save clip as", self._out_path.text(),
                                              f"Video files ({VIDEO_SUFFIXES});;All files (*)")
        if path:
            self._out_path.setText(path)

    def start_export(self) -> None:
        if not self._info or self._exporter.running:
            return
        start, end = self._timeline.in_point, self._timeline.out_point
        if end - start < self._info.frame_duration:
            QMessageBox.information(self, "Nothing to export",
                                    "The in and out points are at the same place.")
            return
        output = self._out_path.text().strip()
        if not output:
            QMessageBox.information(self, "No destination", "Choose where to save the clip.")
            return
        if os.path.abspath(output) == os.path.abspath(self._info.path):
            QMessageBox.warning(self, "Same file",
                                "The clip would overwrite the file you are cutting.")
            return
        folder = os.path.dirname(output) or "."
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "No such folder", f"{folder} does not exist.")
            return

        self._player.pause()
        plan = export.build(self._info, start, end, output, self._keyframes,
                            self._options())
        # Pin down what is being cut now, while it is unambiguous.
        self._exported = Exported(source=self._info.path, output=output,
                                  duration=self._info.duration, kept=end - start)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._reveal_btn.setVisible(False)
        self._export_btn.setEnabled(False)
        self._status.setText("Copying…" if plan.lossless
                             else f"Encoding with {plan.label}…")
        self._exporter.start(plan)

    def _on_export_progress(self, fraction: float, speed: float, eta: float) -> None:
        self._progress.setValue(int(fraction * 1000))
        parts = [f"{fraction * 100:.0f}%"]
        if speed:
            parts.append(f"{speed:.2f}×")
        if eta >= 1:
            parts.append(f"about {eta:.0f}s left")
        self._status.setText("  ·  ".join(parts))

    def _on_export_finished(self, ok: bool, message: str) -> None:
        self._progress.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._export_btn.setEnabled(True)
        self._status.setText(message)
        self._reveal_btn.setVisible(ok)
        if ok and self._info:
            self._last_output = self._out_path.text().strip()
            # Don't let the next export silently overwrite this one.
            self._out_path.setText(export.default_output(
                self._info.path, self._options().ext_for(self._info)))
            if self._delete_source.isChecked():
                # Let the "Saved…" message paint before a modal dialog covers it.
                QTimer.singleShot(0, self._maybe_delete_source)

    # ----- deleting the source ------------------------------------------

    @staticmethod
    def _delete_candidate(record: Exported | None) -> tuple[str, str] | None:
        """Which file may be offered for deletion, and the clip that earns it.

        Separate from the prompt so the decision can be tested without driving a
        modal dialog -- it is the one piece of logic here that can destroy data.
        Returns None whenever deletion must not be offered at all.
        """
        if record is None:
            return None
        # Deliberately the captured source, not whatever is open now: another
        # file may have been opened while this export was encoding.
        source, clip = record.source, record.output
        if not source or not clip:
            return None
        # Refuse unless the clip is demonstrably real. Deleting an original on
        # the strength of a zero-byte output would be a disaster.
        if not os.path.isfile(clip) or os.path.getsize(clip) < MIN_CLIP_BYTES:
            return None
        if not os.path.isfile(source):
            return None
        if os.path.abspath(clip) == os.path.abspath(source):
            return None
        return source, clip

    def _maybe_delete_source(self) -> None:
        """Offer to remove the file we just cut from. Never acts unprompted."""
        record, self._exported = self._exported, None
        candidate = self._delete_candidate(record)
        if candidate is None:
            if record is not None:
                self._status.setText(
                    "Kept the original — the exported clip could not be verified.")
            return
        source, clip = candidate

        size_mb = os.path.getsize(source) / 1_000_000
        kept = record.kept
        share = kept / record.duration * 100 if record.duration else 0

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete the original?")
        box.setText(f"Delete “{os.path.basename(source)}”?")
        box.setInformativeText(
            f"{size_mb:.0f} MB  ·  {fmt_tc(self._info.duration)} long\n\n"
            f"Your clip kept {kept:.1f}s of it — about {share:.0f}%. "
            f"The other {100 - share:.0f}% is only in this file, and cannot be "
            f"recovered from the clip.\n\n"
            f"The clip you just exported is not affected:\n{os.path.basename(clip)}")
        trash = box.addButton("Move to Trash", QMessageBox.ButtonRole.AcceptRole)
        forever = box.addButton("Delete permanently", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep the file", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(trash)     # recoverable option is the default
        box.setEscapeButton(keep)
        box.exec()

        if box.clickedButton() is keep:
            self._status.setText(f"Kept {os.path.basename(source)}.")
            return

        if box.clickedButton() is forever:
            # Irreversible, so it gets its own deliberate confirmation.
            confirm = QMessageBox(self)
            confirm.setIcon(QMessageBox.Icon.Critical)
            confirm.setWindowTitle("Permanently delete?")
            confirm.setText(f"Permanently delete “{os.path.basename(source)}”?")
            confirm.setInformativeText(
                "This does not go to the Trash. It cannot be undone by you, by "
                "this app, or by the file manager.")
            yes = confirm.addButton("Delete permanently", QMessageBox.ButtonRole.DestructiveRole)
            no = confirm.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            confirm.setDefaultButton(no)
            confirm.setEscapeButton(no)
            confirm.exec()
            if confirm.clickedButton() is not yes:
                self._status.setText(f"Kept {os.path.basename(source)}.")
                return
            ok, err = self._delete_forever(source)
        else:
            ok, err = self._move_to_trash(source)

        if not ok:
            QMessageBox.warning(self, "Could not delete",
                                f"{os.path.basename(source)}\n\n{err}")
            return

        where = "Deleted" if box.clickedButton() is forever else "Moved to Trash:"
        self._forget(source)
        # Only let go of the file if it is the one that just went away; another
        # video may have been opened while this export was running.
        if self._info and os.path.abspath(self._info.path) == os.path.abspath(source):
            self._close_source()
        self._status.setText(f"{where} {os.path.basename(source)}  ·  "
                             f"clip saved as {os.path.basename(clip)}")

    @staticmethod
    def _move_to_trash(path: str) -> tuple[bool, str]:
        """Hand the file to the desktop's wastebasket via gio.

        gio picks the right .Trash-1000 for the file's own filesystem, which a
        naive move into ~/.local/share/Trash would get wrong for external drives.
        """
        try:
            result = subprocess.run(["gio", "trash", "--", path],
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"Could not run gio: {exc}"
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip() or "gio trash failed"

    @staticmethod
    def _delete_forever(path: str) -> tuple[bool, str]:
        try:
            os.remove(path)
        except OSError as exc:
            return False, str(exc)
        return True, ""

    def _forget(self, path: str) -> None:
        self._settings.setValue(
            "recent", [p for p in self._recent() if p != path])

    def _close_source(self) -> None:
        """Let go of a file that no longer exists."""
        self._player.stop()
        self._player.setSource(QUrl())
        self._keyframe_loader.cancel()
        self._waveform_loader.cancel()
        self._thumb_loader.cancel()
        self._info = None
        self._keyframes = []
        self._timeline.reset(0.0)
        self._set_loaded(False)
        self._badge.setText("")
        self._title.setText("No file loaded")
        self._meta.setText("Open a video to begin")
        self.setWindowTitle("Clipper")
        self._out_path.clear()
        self._sel_label.setText("—")
        self._update_clock(0)

    def _reveal(self) -> None:
        """Open the file manager with the exported clip selected."""
        path = self._last_output
        if not path or not os.path.exists(path):
            return
        try:
            # Dolphin, Nautilus and Nemo all implement this interface, and it
            # selects the file rather than just opening its folder.
            subprocess.Popen(
                ["dbus-send", "--session", "--dest=org.freedesktop.FileManager1",
                 "--type=method_call", "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1.ShowItems",
                 f"array:string:file://{path}", "string:"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ----- keyboard -----------------------------------------------------

    def _release_text_focus(self) -> bool:
        """Hand the keyboard back to the player. True if a field gave it up."""
        # This window's focus widget, not the application's: the latter is None
        # whenever the window is not the active one.
        focused = self.focusWidget()
        if isinstance(focused, QLineEdit):
            focused.clearFocus()
            return True
        return False

    def eventFilter(self, obj, event):
        """Any click outside a text field hands the keyboard back.

        This has to be an application-level filter rather than the window's own
        mousePressEvent: buttons and dropdowns swallow the click before it can
        reach the window, which would leave a timecode field still holding the
        keyboard and Space still typing into it.
        """
        if (event.type() == QEvent.Type.MouseButtonPress
                and not isinstance(obj, QLineEdit)):
            self._release_text_focus()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        # Only keys the focused widget ignored reach here, so typing a timecode
        # is never intercepted.
        key, mods = event.key(), event.modifiers()
        shift = mods & Qt.KeyboardModifier.ShiftModifier
        ctrl = mods & Qt.KeyboardModifier.ControlModifier

        if key == Qt.Key.Key_Space:
            self.toggle_play()
        elif key == Qt.Key.Key_Left:
            self._nudge(-1.0) if shift else self._step(-1)
        elif key == Qt.Key.Key_Right:
            self._nudge(1.0) if shift else self._step(1)
        elif key == Qt.Key.Key_J:
            self._nudge(-10.0)
        elif key == Qt.Key.Key_L:
            self._nudge(10.0)
        elif key == Qt.Key.Key_K:
            self._player.pause()
        elif key == Qt.Key.Key_I:
            self.set_in()
        elif key == Qt.Key.Key_S:
            self.snap_in_to_keyframe()
        elif key == Qt.Key.Key_Z:
            self._timeline.zoom_all() if self._timeline.zoomed \
                else self._timeline.zoom_to_selection()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._timeline.zoom_in()
        elif key == Qt.Key.Key_Minus:
            self._timeline.zoom_out()
        elif key == Qt.Key.Key_O and not ctrl:
            self.set_out()
        elif key == Qt.Key.Key_O and ctrl:
            self.open_file()
        elif key == Qt.Key.Key_Home:
            self._seek(0)
        elif key == Qt.Key.Key_End:
            self._seek(self._info.duration if self._info else 0)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ctrl:
            self.start_export()
        elif key == Qt.Key.Key_Escape:
            if not self._release_text_focus() and self._exporter.running:
                self._exporter.cancel()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._exporter.running:
            self._exporter.cancel()
        # Stop filtering application-wide events and let go of the media stack
        # before the window goes away. A closed window has no business watching
        # clicks, and leaving the player attached makes teardown order matter.
        QApplication.instance().removeEventFilter(self)
        for loader in (self._keyframe_loader, self._waveform_loader,
                       self._thumb_loader):
            loader.cancel()
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setVideoOutput(None)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Clipper")
    app.setApplicationDisplayName("Clipper")
    # Lets Wayland match the window to the installed .desktop entry, so the task
    # manager shows the right name and icon.
    app.setDesktopFileName("clipper")

    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clipper.svg")
    app.setWindowIcon(QIcon(icon) if os.path.exists(icon)
                      else QIcon.fromTheme("multimedia-video-player"))

    path = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if path and not os.path.exists(os.path.expanduser(path)):
        print(f"clipper: no such file: {path}", file=sys.stderr)
        return 1

    window = Clipper(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
