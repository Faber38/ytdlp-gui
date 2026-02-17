#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import re
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any

from PySide6.QtCore import QObject, Signal, QThread, Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QFileDialog, QComboBox, QProgressBar,
    QMessageBox, QCheckBox
)

# yt-dlp Python API
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


# ---------------------------
# Helpers
# ---------------------------

def default_videos_dir() -> str:
    return str(Path.home() / "Videos")


def normalize_youtube_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    if not re.search(r"(youtube\.com|youtu\.be)", url, re.IGNORECASE):
        return ""

    m = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})", url, re.IGNORECASE)
    if m:
        vid = m.group(1)
        return f"https://www.youtube.com/watch?v={vid}"

    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", url, re.IGNORECASE)
    if m:
        vid = m.group(1)
        return f"https://www.youtube.com/watch?v={vid}"

    return url


def is_ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") or shutil.which("ffmpeg.exe"))


def ffmpeg_install_hint() -> str:
    if sys.platform.startswith("win"):
        return (
            "FFmpeg ist nicht installiert.\n\n"
            "Installiere es unter Windows mit:\n"
            "winget install Gyan.FFmpeg\n\n"
            "Danach Terminal/Explorer neu starten."
        )
    return (
        "FFmpeg ist nicht installiert.\n\n"
        "Installiere es unter Linux mit:\n"
        "sudo apt install ffmpeg"
    )


def _browser_profile_exists(browser: str) -> bool:
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")

    if browser == "firefox":
        return (Path(appdata) / "Mozilla" / "Firefox" / "Profiles").exists()
    if browser == "chrome":
        return (Path(localappdata) / "Google" / "Chrome" / "User Data").exists()
    if browser == "edge":
        return (Path(localappdata) / "Microsoft" / "Edge" / "User Data").exists()
    return False


def pick_cookie_browser() -> Optional[str]:
    # Reihenfolge für Windows praxisnah:
    if sys.platform.startswith("win"):
        for b in ("chrome", "edge", "firefox"):
            if _browser_profile_exists(b):
                return b
        return None
    # Linux:
    return "firefox"


def build_format(q: str, audio_only: bool) -> str:
    if audio_only:
        return "ba/b"
    if q == "best":
        # mp4-prefer, dann fallback
        return "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
    # limit by height, mp4 prefer
    return f"bv*[height<={q}][ext=mp4]+ba[ext=m4a]/bv*[height<={q}]+ba/b"


# ---------------------------
# Worker (runs in background thread)
# ---------------------------

class DownloadWorker(QObject):
    log = Signal(str)
    progress = Signal(int, str)   # percent, info text
    finished = Signal(bool)       # success

    def __init__(
        self,
        urls: List[str],
        outdir: str,
        q: str,
        audio_only: bool,
        allow_playlist: bool,
        use_cookies: bool,
    ):
        super().__init__()
        self.urls = urls
        self.outdir = outdir
        self.q = q
        self.audio_only = audio_only
        self.allow_playlist = allow_playlist
        self.use_cookies = use_cookies

        self._cookie_browser = pick_cookie_browser() if use_cookies else None

    def _hook(self, d: Dict[str, Any]) -> None:
        # Called by yt-dlp from worker thread -> emit signals only!
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = 0
            if total:
                pct = int(max(0, min(100, (downloaded * 100) / total)))
            speed = d.get("_speed_str") or ""
            eta = d.get("_eta_str") or ""
            self.progress.emit(pct, " • ".join(x for x in [speed, f"ETA {eta}".strip()] if x))
        elif status == "finished":
            self.progress.emit(100, "Fertig (Postprocessing)")

    def _make_opts(self, with_cookies: bool) -> Dict[str, Any]:
        fmt = build_format(self.q, self.audio_only)

        # Output template similar to your earlier tool
        outtmpl = str(Path(self.outdir) / "%(title).200B [%(id)s].%(ext)s")

        opts: Dict[str, Any] = {
            "format": fmt,
            "outtmpl": outtmpl,
            "noplaylist": not self.allow_playlist,
            "retries": 15,
            "fragment_retries": 15,
            "concurrent_fragment_downloads": 1,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._hook],
            # logs:
            "logger": _YTDLPLogger(self.log),
        }

        # Merge to mp4 for video
        if not self.audio_only:
            opts["merge_output_format"] = "mp4"

        # Audio extraction
        if self.audio_only:
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"},
            ]

        # Cookies
        if with_cookies and self._cookie_browser:
            # yt-dlp python options use same key name as CLI:
            opts["cookiesfrombrowser"] = (self._cookie_browser,)

        return opts

    def run(self) -> None:
        try:
            Path(self.outdir).mkdir(parents=True, exist_ok=True)

            # First pass (maybe with cookies)
            ok = self._run_all(with_cookies=self.use_cookies)
            if ok:
                self.finished.emit(True)
                return

            # If failed AND cookies were enabled -> retry without cookies once
            if self.use_cookies:
                self.log.emit("\n⚠ Fehlgeschlagen mit Cookies → Retry ohne Cookies…\n")
                ok2 = self._run_all(with_cookies=False)
                self.finished.emit(ok2)
                return

            self.finished.emit(False)

        except Exception as e:
            self.log.emit(f"\n✘ Unerwarteter Fehler: {e}\n")
            self.finished.emit(False)

    def _run_all(self, with_cookies: bool) -> bool:
        # If cookies requested but no browser profile: skip cookies silently
        if with_cookies and not self._cookie_browser:
            self.log.emit("⚠ Cookies aktiviert, aber kein Browser-Profil gefunden (Chrome/Edge/Firefox). -> ohne Cookies.\n")
            with_cookies = False

        opts = self._make_opts(with_cookies=with_cookies)

        # One YoutubeDL instance for the batch
        try:
            with YoutubeDL(opts) as ydl:
                for idx, url in enumerate(self.urls, start=1):
                    self.progress.emit(0, "")
                    self.log.emit(f"\n== [{idx}/{len(self.urls)}] {url} ==\n")
                    ydl.download([url])
            return True
        except DownloadError as e:
            self.log.emit(f"\n✘ DownloadError: {e}\n")
            return False
        except Exception as e:
            self.log.emit(f"\n✘ Fehler: {e}\n")
            return False


class _YTDLPLogger:
    """
    yt-dlp expects logger with debug/info/warning/error methods.
    We forward into Qt signal safely (as text).
    """
    def __init__(self, log_signal: Signal):
        self._log_signal = log_signal

    def debug(self, msg: str) -> None:
        # yt-dlp can be noisy; keep only relevant lines
        if msg:
            self._log_signal.emit(str(msg))

    def info(self, msg: str) -> None:
        if msg:
            self._log_signal.emit(str(msg))

    def warning(self, msg: str) -> None:
        if msg:
            self._log_signal.emit("⚠ " + str(msg))

    def error(self, msg: str) -> None:
        if msg:
            self._log_signal.emit("✘ " + str(msg))


# ---------------------------
# GUI
# ---------------------------

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YT-DLP GUI (Python API)")
        self.resize(900, 600)

        self.thread: Optional[QThread] = None
        self.worker: Optional[DownloadWorker] = None

        self.url_edit = QLineEdit()
        self.q_combo = QComboBox()
        self.q_combo.addItems(["best", "1080", "720", "480", "360"])

        self.audio_chk = QCheckBox("Audio-only (mp3)")
        self.playlist_chk = QCheckBox("Playlist erlauben")
        self.cookies_chk = QCheckBox("Cookies aus Browser verwenden")

        self.outdir_edit = QLineEdit(default_videos_dir())
        self.btn_outdir = QPushButton("Zielordner…")
        self.btn_start = QPushButton("Start")
        self.btn_start_batch = QPushButton("Batch (aus Datei)…")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress_info = QLabel("")

        self.log = QTextEdit()
        self.log.setReadOnly(True)

        self._build_ui()
        self._log("Hinweis: ffmpeg wird für MP4-Merge/Audio-Extraktion benötigt.\n")

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # URL row
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("URL:"))
        r1.addWidget(self.url_edit, 1)
        layout.addLayout(r1)

        # Options
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Qualität:"))
        r2.addWidget(self.q_combo)
        r2.addSpacing(10)
        r2.addWidget(self.audio_chk)
        r2.addWidget(self.playlist_chk)
        r2.addWidget(self.cookies_chk)
        layout.addLayout(r2)

        # outdir
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Ziel:"))
        r3.addWidget(self.outdir_edit, 1)
        self.btn_outdir.clicked.connect(self.choose_outdir)
        r3.addWidget(self.btn_outdir)
        layout.addLayout(r3)

        # buttons
        r4 = QHBoxLayout()
        self.btn_start.clicked.connect(self.start_single)
        self.btn_start_batch.clicked.connect(self.start_batch_from_file)
        r4.addWidget(self.btn_start)
        r4.addWidget(self.btn_start_batch)
        r4.addStretch(1)
        layout.addLayout(r4)

        # progress
        r5 = QHBoxLayout()
        r5.addWidget(self.progress, 1)
        r5.addWidget(self.progress_info)
        layout.addLayout(r5)

        layout.addWidget(QLabel("Log:"))
        layout.addWidget(self.log, 1)

    def _log(self, msg: str) -> None:
        self.log.append(str(msg))

    def choose_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "Zielordner wählen", self.outdir_edit.text().strip())
        if d:
            self.outdir_edit.setText(d)

    def _set_running(self, running: bool):
        self.btn_start.setEnabled(not running)
        self.btn_start_batch.setEnabled(not running)
        self.btn_outdir.setEnabled(not running)

    def _validate_common(self) -> Optional[str]:
        # ffmpeg required for mp4 merge or mp3 extraction
        if not is_ffmpeg_available():
            QMessageBox.critical(self, "FFmpeg fehlt", ffmpeg_install_hint())
            return None

        outdir = self.outdir_edit.text().strip()
        if not outdir:
            QMessageBox.warning(self, "Fehler", "Bitte Zielordner setzen.")
            return None

        return outdir

    def start_single(self):
        outdir = self._validate_common()
        if not outdir:
            return

        url = self.url_edit.text().strip()
        url = normalize_youtube_url(url)
        if not url:
            QMessageBox.warning(self, "Fehler", "Bitte gültige YouTube-URL eingeben.")
            return

        self._start_worker(
            urls=[url],
            outdir=outdir,
            q=self.q_combo.currentText(),
            audio_only=self.audio_chk.isChecked(),
            allow_playlist=self.playlist_chk.isChecked(),
            use_cookies=self.cookies_chk.isChecked(),
        )

    def start_batch_from_file(self):
        outdir = self._validate_common()
        if not outdir:
            return

        file, _ = QFileDialog.getOpenFileName(self, "URL-Datei wählen", "", "Text (*.txt);;Alle Dateien (*)")
        if not file:
            return

        try:
            lines = Path(file).read_text(encoding="utf-8").splitlines()
        except Exception as e:
            QMessageBox.warning(self, "Fehler", f"Datei konnte nicht gelesen werden: {e}")
            return

        urls = []
        for ln in lines:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            u = normalize_youtube_url(ln)
            if u:
                urls.append(u)

        if not urls:
            QMessageBox.information(self, "Keine URLs", "Keine gültigen YouTube-URLs gefunden.")
            return

        self._start_worker(
            urls=urls,
            outdir=outdir,
            q=self.q_combo.currentText(),
            audio_only=self.audio_chk.isChecked(),
            allow_playlist=self.playlist_chk.isChecked(),
            use_cookies=self.cookies_chk.isChecked(),
        )

    def _start_worker(self, urls: List[str], outdir: str, q: str, audio_only: bool, allow_playlist: bool, use_cookies: bool):
        if self.thread is not None:
            return

        self.progress.setValue(0)
        self.progress_info.setText("")
        self._set_running(True)

        self.thread = QThread()
        self.worker = DownloadWorker(
            urls=urls,
            outdir=outdir,
            q=q,
            audio_only=audio_only,
            allow_playlist=allow_playlist,
            use_cookies=use_cookies,
        )
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)

        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup_thread)

        self.thread.start()

    def _on_progress(self, percent: int, info: str):
        self.progress.setValue(percent)
        self.progress_info.setText(info or "")

    def _on_finished(self, ok: bool):
        if ok:
            self._log("\n✔ Fertig.\n")
        else:
            self._log("\n✘ Fehlgeschlagen.\n")
        self._set_running(False)

    def _cleanup_thread(self):
        self.worker = None
        self.thread = None


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
