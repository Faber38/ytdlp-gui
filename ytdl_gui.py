#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import re
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any

from PySide6.QtGui import QFont
from PySide6.QtCore import QObject, Signal, QThread, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QComboBox,
    QProgressBar,
    QMessageBox,
    QCheckBox,
    QGroupBox,
    QSizePolicy,
)

# yt-dlp Python API
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

import subprocess


def get_git_version():
    try:
        return (
            subprocess.check_output(
                ["git", "describe", "--tags", "--always"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return APP_VERSION


APP_VERSION = get_git_version()


# ---------------------------
# UI Styling (global)
# ---------------------------

APP_QSS = """
QWidget {
    font-size: 11pt;
    background-color: #f4f6f9;
    color: #2c3e50;
}

QGroupBox {
    border: 1px solid #d0d7de;
    border-radius: 12px;
    margin-top: 10px;
    background: white;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    font-weight: 700;
    color: #1f2937;
}

QLabel#AppTitle {
    font-size: 18pt;
    font-weight: 800;
    color: #111827;
}

QLabel#AppSubTitle {
    color: #6b7280;
}

QLabel#StatusBadge {
    padding: 6px 10px;
    border-radius: 999px;
    background: #eef2ff;
    color: #3730a3;
    font-weight: 700;
}

QLineEdit, QComboBox {
    padding: 8px 10px;
    border: 1px solid #d0d7de;
    border-radius: 10px;
    background: white;
}

QLineEdit:focus, QComboBox:focus {
    border: 2px solid #ff0033;
}

QPushButton {
    padding: 9px 14px;
    border-radius: 12px;
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    color: #111827;
    font-weight: 650;
}

QPushButton:hover {
    background-color: #f3f4f6;
}

QPushButton#PrimaryBtn {
    background-color: #ff0033;
    color: white;
    border: none;
}

QPushButton#PrimaryBtn:hover {
    background-color: #cc0028;
}

QPushButton:disabled {
    background-color: #e5e7eb;
    color: #6b7280;
    border: 1px solid #e5e7eb;
}

QCheckBox {
    spacing: 7px;
}

QProgressBar {
    border: none;
    border-radius: 10px;
    background: #e3e6ea;
    height: 18px;
}

QProgressBar::chunk {
    border-radius: 10px;
    background-color: #ff0033;
}

QTextEdit {
    background: #111827;
    color: #e5e7eb;
    border-radius: 12px;
    padding: 10px;
    font-family: Consolas, monospace;
}

QScrollBar:vertical {
    border: none;
    background: #0b1220;
    width: 10px;
}

QScrollBar::handle:vertical {
    background: #374151;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #4b5563;
}
"""


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
    # Windows: nimm den, der wirklich existiert
    if sys.platform.startswith("win"):
        for b in ("chrome", "edge", "firefox"):
            if _browser_profile_exists(b):
                return b
        return None
    # Linux: firefox ist häufig vorhanden – wenn nicht, fällt es ohnehin sauber zurück
    return "firefox"


def build_format(q: str, audio_only: bool) -> str:
    if audio_only:
        return "ba/b"
    if q == "best":
        return "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
    return f"bv*[height<={q}][ext=mp4]+ba[ext=m4a]/bv*[height<={q}]+ba/b"


# ---------------------------
# Worker (runs in background thread)
# ---------------------------


class _YTDLPLogger:
    """yt-dlp expects logger with debug/info/warning/error methods."""

    def __init__(self, log_signal: Signal):
        self._log_signal = log_signal

    def debug(self, msg: str) -> None:
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


class DownloadWorker(QObject):
    log = Signal(str)
    progress = Signal(int, str)  # percent, info text
    finished = Signal(bool)  # success

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
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = 0
            if total:
                pct = int(max(0, min(100, (downloaded * 100) / total)))
            speed = d.get("_speed_str") or ""
            eta = d.get("_eta_str") or ""
            info = " • ".join(x for x in [speed, f"ETA {eta}".strip()] if x)
            self.progress.emit(pct, info)
        elif status == "finished":
            self.progress.emit(100, "Fertig (Postprocessing)")

    def _make_opts(self, with_cookies: bool) -> Dict[str, Any]:
        fmt = build_format(self.q, self.audio_only)
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
            "logger": _YTDLPLogger(self.log),
        }

        if not self.audio_only:
            opts["merge_output_format"] = "mp4"

        if self.audio_only:
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"},
            ]

        if with_cookies and self._cookie_browser:
            opts["cookiesfrombrowser"] = (self._cookie_browser,)

        return opts

    def run(self) -> None:
        try:
            Path(self.outdir).mkdir(parents=True, exist_ok=True)

            ok = self._run_all(with_cookies=self.use_cookies)
            if ok:
                self.finished.emit(True)
                return

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
        if with_cookies and not self._cookie_browser:
            self.log.emit(
                "⚠ Cookies aktiviert, aber kein Browser-Profil gefunden (Chrome/Edge/Firefox). -> ohne Cookies.\n"
            )
            with_cookies = False

        opts = self._make_opts(with_cookies=with_cookies)

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


# ---------------------------
# GUI
# ---------------------------


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"YT-DLP GUI (Python API) {APP_VERSION}")
        self.resize(980, 680)

        self.thread: Optional[QThread] = None
        self.worker: Optional[DownloadWorker] = None

        # buffered log (massive performance win)
        self._log_buffer: List[str] = []
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(100)
        self._log_flush_timer.timeout.connect(self._flush_log)
        self._log_flush_timer.start()

        # widgets
        self.url_edit = QLineEdit()
        self.q_combo = QComboBox()
        self.q_combo.addItems(["best", "1080", "720", "480", "360"])

        self.audio_chk = QCheckBox("Audio-only (mp3)")
        self.playlist_chk = QCheckBox("Playlist erlauben")
        self.cookies_chk = QCheckBox("Cookies aus Browser verwenden")

        self.outdir_edit = QLineEdit(default_videos_dir())
        self.btn_outdir = QPushButton("Zielordner…")
        self.btn_clipboard = QPushButton("Clipboard")
        self.btn_clear = QPushButton("Leeren")

        self.btn_start = QPushButton("Download starten")
        self.btn_start.setObjectName("PrimaryBtn")
        self.btn_start.setDefault(True)

        self.btn_start_batch = QPushButton("Batch (aus Datei)…")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress_info = QLabel("")
        self.progress_info.setMinimumWidth(220)

        self.status_badge = QLabel("Bereit")
        self.status_badge.setObjectName("StatusBadge")

        self.log = QTextEdit()
        self.log.setReadOnly(True)

        # layout + polish
        self._build_ui()
        self._apply_polish()
        self._log("Hinweis: ffmpeg wird für MP4-Merge/Audio-Extraktion benötigt.\n")

    # ----- UI

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # Header
        header = QGroupBox()
        header.setTitle("")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 14, 16, 14)

        left = QVBoxLayout()
        title = QLabel(f"YT-DLP GUI  {APP_VERSION}")
        title.setObjectName("AppTitle")
        subtitle = QLabel(
            "Python API • MP4 bevorzugt • Batch Downloads • Cookies optional"
        )
        subtitle.setObjectName("AppSubTitle")
        left.addWidget(title)
        left.addWidget(subtitle)
        h.addLayout(left, 1)
        h.addWidget(self.status_badge, 0, Qt.AlignRight | Qt.AlignVCenter)

        root.addWidget(header)

        # Input card
        card = QGroupBox("Download")
        root.addWidget(card)
        c = QVBoxLayout(card)
        c.setContentsMargins(16, 16, 16, 16)
        c.setSpacing(10)

        # URL row + small buttons
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("URL:"))
        r1.addWidget(self.url_edit, 1)
        self.btn_clipboard.clicked.connect(self.paste_clipboard)
        self.btn_clear.clicked.connect(lambda: self.url_edit.setText(""))
        r1.addWidget(self.btn_clipboard)
        r1.addWidget(self.btn_clear)
        c.addLayout(r1)

        # Options
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Qualität:"))
        r2.addWidget(self.q_combo)
        r2.addSpacing(10)
        r2.addWidget(self.audio_chk)
        r2.addWidget(self.playlist_chk)
        r2.addWidget(self.cookies_chk)
        r2.addStretch(1)
        c.addLayout(r2)

        # Outdir
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Ziel:"))
        r3.addWidget(self.outdir_edit, 1)
        self.btn_outdir.clicked.connect(self.choose_outdir)
        r3.addWidget(self.btn_outdir)
        c.addLayout(r3)

        # Action buttons
        r4 = QHBoxLayout()
        self.btn_start.clicked.connect(self.start_single)
        self.btn_start_batch.clicked.connect(self.start_batch_from_file)
        r4.addWidget(self.btn_start)
        r4.addWidget(self.btn_start_batch)
        r4.addStretch(1)
        c.addLayout(r4)

        # Progress
        r5 = QHBoxLayout()
        r5.addWidget(self.progress, 1)
        r5.addWidget(self.progress_info)
        c.addLayout(r5)

        # Log card
        log_card = QGroupBox("Log")
        root.addWidget(log_card, 1)
        lc = QVBoxLayout(log_card)
        lc.setContentsMargins(16, 16, 16, 16)
        lc.addWidget(self.log)

    def _apply_polish(self) -> None:
        # monospace log
        log_font = QFont("Monospace")
        log_font.setStyleHint(QFont.Monospace)
        self.log.setFont(log_font)

        # consistent heights
        for b in (
            self.btn_start,
            self.btn_start_batch,
            self.btn_outdir,
            self.btn_clipboard,
            self.btn_clear,
        ):
            b.setMinimumHeight(36)

        # make URL a bit taller
        self.url_edit.setMinimumHeight(36)
        self.outdir_edit.setMinimumHeight(36)

        self.progress.setMinimumHeight(20)

    # ----- logging (buffered)

    def _log(self, msg: str) -> None:
        self._log_buffer.append(str(msg))

    def _flush_log(self) -> None:
        if not self._log_buffer:
            return
        chunk = "\n".join(self._log_buffer)
        self._log_buffer.clear()
        self.log.append(chunk)

    # ----- actions

    def paste_clipboard(self):
        txt = QApplication.clipboard().text().strip()
        if txt:
            self.url_edit.setText(txt)

    def choose_outdir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Zielordner wählen", self.outdir_edit.text().strip()
        )
        if d:
            self.outdir_edit.setText(d)

    def _set_running(self, running: bool):
        self.btn_start.setEnabled(not running)
        self.btn_start_batch.setEnabled(not running)
        self.btn_outdir.setEnabled(not running)
        self.btn_clipboard.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.status_badge.setText("Download läuft…" if running else "Bereit")

    def _validate_common(self) -> Optional[str]:
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

        url = normalize_youtube_url(self.url_edit.text().strip())
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

        file, _ = QFileDialog.getOpenFileName(
            self, "URL-Datei wählen", "", "Text (*.txt);;Alle Dateien (*)"
        )
        if not file:
            return

        try:
            lines = Path(file).read_text(encoding="utf-8").splitlines()
        except Exception as e:
            QMessageBox.warning(
                self, "Fehler", f"Datei konnte nicht gelesen werden: {e}"
            )
            return

        urls: List[str] = []
        for ln in lines:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            u = normalize_youtube_url(ln)
            if u:
                urls.append(u)

        if not urls:
            QMessageBox.information(
                self, "Keine URLs", "Keine gültigen YouTube-URLs gefunden."
            )
            return

        self._start_worker(
            urls=urls,
            outdir=outdir,
            q=self.q_combo.currentText(),
            audio_only=self.audio_chk.isChecked(),
            allow_playlist=self.playlist_chk.isChecked(),
            use_cookies=self.cookies_chk.isChecked(),
        )

    def _start_worker(
        self,
        urls: List[str],
        outdir: str,
        q: str,
        audio_only: bool,
        allow_playlist: bool,
        use_cookies: bool,
    ):
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
    app.setStyleSheet(APP_QSS)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
