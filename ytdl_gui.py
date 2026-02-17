import os
import re
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sys
from pathlib import Path


def app_dir() -> Path:
    """
    Bei PyInstaller --onefile:
      - Dateien werden in ein Temp-Verzeichnis entpackt (sys._MEIPASS)
    Beim normalen Python-Start:
      - Ordner der .py Datei
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


# ------------------------------------------------------------
# Gebundelte Tools (wenn per PyInstaller --add-binary enthalten)
# ------------------------------------------------------------
if sys.platform.startswith("win"):
    # Windows: ytdl_gui.exe enthält (geplant) deno.exe + yt-dlp.exe
    DENO = str(app_dir() / "deno.exe")
    YT_DLP = str(app_dir() / "yt-dlp.exe")
else:
    # Linux: optional gebundeltes yt-dlp, ansonsten System
    DENO = "/usr/local/bin/deno"
    YT_DLP = str(app_dir() / "yt-dlp")

# Fallback, wenn du das Script lokal startest (ohne Bundle-Dateien)
if not sys.platform.startswith("win") and not os.path.exists(YT_DLP):
    YT_DLP = "yt-dlp"

# ffmpeg bleibt System-Tool (groß, nicht bundlen)
FFMPEG = "ffmpeg"

EJS_MODE = "ejs:npm"
EXTRACTOR_ARGS = "youtube:player_client=web,-tv,-web_safari,-ios,-android_sdkless"

# ---------------------------
# Defaults (wie dein Bash-Script)
# ---------------------------
DEFAULT_Q = "1080"      # best | 1080 | 720 | 480 | 360 | ...
DEFAULT_NO_PLAYLIST = True


def videos_dir() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, "Videos")


def is_tool_available(cmd: str) -> bool:
    """
    Prüft, ob ein Tool ausführbar ist:
    - wenn cmd ein Pfad ist: muss existieren + ausführbar sein
    - wenn cmd nur Name ist: versuchen wir cmd --version
    """
    if os.path.sep in cmd or cmd.lower().endswith(".exe"):
        return os.path.exists(cmd) and os.access(cmd, os.X_OK)

    try:
        subprocess.run([cmd, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except Exception:
        return False


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


def build_format(q: str) -> str:
    if q == "best":
        return "bv*[protocol!=m3u8]+ba/b[protocol!=m3u8]"
    return f"bv*[height<={q}][protocol!=m3u8]+ba/b[height<={q}][protocol!=m3u8]"


def _browser_profile_exists(browser: str) -> bool:
    """
    Grobe Heuristik: Browserprofil-Verzeichnisse prüfen.
    Reicht für "geht bei fast allen" ohne Registry-Kram.
    """
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")

    if browser == "firefox":
        # %APPDATA%\Mozilla\Firefox\Profiles
        p = Path(appdata) / "Mozilla" / "Firefox" / "Profiles"
        return p.exists()

    if browser == "chrome":
        # %LOCALAPPDATA%\Google\Chrome\User Data
        p = Path(localappdata) / "Google" / "Chrome" / "User Data"
        return p.exists()

    if browser == "edge":
        # %LOCALAPPDATA%\Microsoft\Edge\User Data
        p = Path(localappdata) / "Microsoft" / "Edge" / "User Data"
        return p.exists()

    return False


def pick_cookie_browser() -> str | None:
    """
    Liefert den Browser-Namen für yt-dlp --cookies-from-browser,
    oder None, wenn keiner sinnvoll verfügbar ist.
    """
    if sys.platform.startswith("win"):
        for b in ("chrome", "edge", "firefox"):
            if _browser_profile_exists(b):
                return b
        return None

    # Linux (dein Setup)
    return "firefox"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("yt-dlp GUI (Clipboard)")
        self.geometry("860x520")

        self.url_var = tk.StringVar()
        self.q_var = tk.StringVar(value=DEFAULT_Q)
        self.audio_only_var = tk.BooleanVar(value=False)
        self.playlist_var = tk.BooleanVar(value=not DEFAULT_NO_PLAYLIST)
        self.cookies_var = tk.BooleanVar(value=True)
        self.outdir_var = tk.StringVar(value=videos_dir())

        self._build_ui()
        self._log("Hinweis: ffmpeg muss installiert sein (System).\n")
        self._log(f"yt-dlp: {YT_DLP}\n")
        self._log(f"deno:  {DENO}\n\n")

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

        # URL
        url_row = ttk.Frame(frm)
        url_row.pack(fill="x", **pad)

        ttk.Label(url_row, text="URL (leer lassen → Clipboard):").pack(anchor="w")
        ttk.Entry(url_row, textvariable=self.url_var).pack(fill="x", expand=True, pady=4)

        btns = ttk.Frame(url_row)
        btns.pack(fill="x")
        ttk.Button(btns, text="Clipboard einfügen", command=self.paste_clipboard).pack(side="left")
        ttk.Button(btns, text="URL normalisieren", command=self.normalize_into_field).pack(side="left", padx=8)

        # Optionen
        opt = ttk.LabelFrame(frm, text="Optionen")
        opt.pack(fill="x", **pad)

        opt_grid = ttk.Frame(opt)
        opt_grid.pack(fill="x", padx=10, pady=10)

        ttk.Label(opt_grid, text="Qualität:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            opt_grid,
            textvariable=self.q_var,
            values=["best", "1080", "720", "480", "360"],
            width=10
        ).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Checkbutton(opt_grid, text="Audio-only (mp3)", variable=self.audio_only_var)\
            .grid(row=0, column=2, sticky="w", padx=12)
        ttk.Checkbutton(opt_grid, text="Playlist erlauben", variable=self.playlist_var)\
            .grid(row=0, column=3, sticky="w", padx=12)
        ttk.Checkbutton(opt_grid, text="Cookies aus Browser", variable=self.cookies_var)\
            .grid(row=0, column=4, sticky="w", padx=12)

        # Zielordner
        out = ttk.Frame(frm)
        out.pack(fill="x", **pad)
        ttk.Label(out, text="Zielordner:").pack(anchor="w")
        out_row = ttk.Frame(out)
        out_row.pack(fill="x")
        ttk.Entry(out_row, textvariable=self.outdir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="Ändern…", command=self.choose_outdir).pack(side="left", padx=8)

        # Start
        run_row = ttk.Frame(frm)
        run_row.pack(fill="x", **pad)
        ttk.Button(run_row, text="Download starten", command=self.start_download).pack(side="left")
        ttk.Button(run_row, text="Log leeren", command=self.clear_log).pack(side="left", padx=8)

        # Log
        logf = ttk.LabelFrame(frm, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    def _log(self, msg: str):
        self.log.insert("end", msg)
        self.log.see("end")

    def clear_log(self):
        self.log.delete("1.0", "end")

    def paste_clipboard(self):
        try:
            txt = self.clipboard_get()
            self.url_var.set((txt or "").strip())
        except Exception:
            messagebox.showwarning("Clipboard", "Konnte Clipboard nicht lesen.")

    def normalize_into_field(self):
        url = self.url_var.get().strip()
        if not url:
            try:
                url = (self.clipboard_get() or "").strip()
            except Exception:
                url = ""
        norm = normalize_youtube_url(url)
        if not norm:
            messagebox.showerror("URL", "Keine gültige YouTube-URL gefunden (Feld/Clipboard).")
            return
        self.url_var.set(norm)

    def choose_outdir(self):
        d = filedialog.askdirectory(initialdir=self.outdir_var.get() or videos_dir())
        if d:
            self.outdir_var.set(d)

    def start_download(self):
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        # yt-dlp
        if not is_tool_available(YT_DLP):
            self._log(f"✘ yt-dlp nicht gefunden/ausführbar: {YT_DLP}\n")
            return

        # ffmpeg (mit sauberer Install-Hilfe)
        if not is_tool_available(FFMPEG):
            self._log("✘ ffmpeg nicht gefunden.\n")

            if sys.platform.startswith("win"):
                msg = (
                    "FFmpeg ist nicht installiert.\n\n"
                    "Installiere es unter Windows mit:\n"
                    "winget install Gyan.FFmpeg\n\n"
                    "Danach Terminal neu starten."
                )
            else:
                msg = (
                    "FFmpeg ist nicht installiert.\n\n"
                    "Installiere es unter Linux mit:\n"
                    "sudo apt install ffmpeg"
                )

            self._log(msg + "\n")
            messagebox.showerror("FFmpeg fehlt", msg)
            return

        # deno
        if sys.platform.startswith("win"):
            if not os.path.exists(DENO):
                self._log(f"✘ deno.exe fehlt (sollte gebundled sein): {DENO}\n")
                return
        else:
            if not os.path.exists(DENO) or not os.access(DENO, os.X_OK):
                self._log(f"✘ deno nicht gefunden/ausführbar unter: {DENO}\n")
                return

        # URL
        url = self.url_var.get().strip()
        if not url:
            try:
                url = (self.clipboard_get() or "").strip()
            except Exception:
                url = ""

        url = normalize_youtube_url(url)
        if not url:
            self._log("✘ Kein gültiger YouTube-Link gefunden.\n")
            return

        outdir = (self.outdir_var.get() or "").strip() or videos_dir()
        os.makedirs(outdir, exist_ok=True)

        q = (self.q_var.get() or DEFAULT_Q).strip()
        audio_only = bool(self.audio_only_var.get())
        allow_playlist = bool(self.playlist_var.get())
        use_cookies = bool(self.cookies_var.get())

        outtpl = os.path.join(outdir, "%(title).200B [%(id)s].%(ext)s")

        self._log(f"➤ URL: {url}\n")
        self._log(f"➤ Ziel: {outdir}\n")
        self._log(f"➤ Qualität: {q}\n")
        self._log(f"➤ Modus: {'Audio (mp3)' if audio_only else 'Video (mp4)'}\n")
        self._log(f"➤ Playlist: {'erlaubt' if allow_playlist else 'blockiert'}\n")

        cookie_browser = None
        if use_cookies:
            cookie_browser = pick_cookie_browser()
            if cookie_browser:
                self._log(f"➤ Cookies: {cookie_browser}\n\n")
            else:
                self._log("⚠ Cookies aktiviert, aber kein Browser-Profil gefunden (Chrome/Edge/Firefox). -> ohne Cookies.\n\n")
                use_cookies = False
        else:
            self._log("➤ Cookies: aus\n\n")

        def make_common_args(with_cookies: bool) -> list[str]:
            args = [
                "--js-runtimes", f"deno:{DENO}",
                "--remote-components", EJS_MODE,
                "--extractor-args", EXTRACTOR_ARGS,
                "--retries", "15",
                "--fragment-retries", "15",
                "--retry-sleep", "3",
                "--concurrent-fragments", "1",
            ]
            if with_cookies and cookie_browser:
                args += ["--cookies-from-browser", cookie_browser]
            return args

        playlist_args = [] if allow_playlist else ["--no-playlist"]

        def run_ytdlp(fmt: str, extra: list[str], with_cookies: bool) -> int:
            common_args = make_common_args(with_cookies)
            cmd = [YT_DLP] + common_args + playlist_args + ["-f", fmt] + extra + ["-o", outtpl, url]
            self._log("CMD:\n" + " ".join(cmd) + "\n\n")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            assert proc.stdout is not None
            for line in proc.stdout:
                self._log(line)
            return proc.wait()

        def do_download(with_cookies: bool) -> int:
            if audio_only:
                return run_ytdlp("ba/b", ["--extract-audio", "--audio-format", "mp3"], with_cookies)

            primary = build_format(q)
            fallback = "18"

            self._log(f"\n➤ Versuch 1: {primary}\n")
            rc1 = run_ytdlp(primary, ["--merge-output-format", "mp4"], with_cookies)

            if rc1 != 0:
                self._log(f"\n⚠ Versuch 1 fehlgeschlagen (Exit {rc1}) → Fallback auf Format 18\n")
                self._log(f"➤ Versuch 2: {fallback}\n")
                return run_ytdlp(fallback, ["--merge-output-format", "mp4"], with_cookies)

            return rc1

        # 1) normaler Versuch (ggf. mit Cookies)
        rc = do_download(with_cookies=use_cookies)

        # 2) Wenn Cookies aktiv waren und es scheitert: EINMAL retry ohne Cookies
        if rc != 0 and use_cookies:
            self._log("\n⚠ Download fehlgeschlagen mit Cookies → Retry ohne Cookies...\n\n")
            rc = do_download(with_cookies=False)

        if rc == 0:
            self._log(f"\n✔ Fertig! Datei liegt in: {outdir}\n")
        else:
            self._log(f"\n✘ Download fehlgeschlagen (Exit {rc}).\n")


if __name__ == "__main__":
    App().mainloop()
