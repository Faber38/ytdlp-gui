# ytdlp-gui

Kleine GUI (tkinter) für **yt-dlp**, die eine YouTube-URL aus dem Clipboard übernimmt
(oder manuell), Shorts/`youtu.be` normalisiert und den Download startet.

## Features
- URL aus Clipboard oder Eingabefeld
- Shorts / youtu.be → `watch?v=...`
- Qualität auswählbar (z.B. 1080/720/480/360 oder `best`)
- Audio-only (MP3)
- Playlist erlauben/blockieren
- Optional: Firefox-Cookies verwenden
- Fallback: wenn der gewünschte Format-Download fehlschlägt → Format 18

## Voraussetzungen (zur Laufzeit)
Die App bringt die Tools **nicht** mit. Diese müssen auf dem System installiert sein:
- `yt-dlp`
- `ffmpeg`
- `deno` (für yt-dlp JS-Extraction; im Code standardmäßig `/usr/local/bin/deno`)

## Build (lokal, Linux)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pyinstaller --noconsole --onefile ytdl_gui.py
./dist/ytdl_gui
