# Setuno

A local, offline companion for **DJs, radio hosts, and playlist curators**. Scans a music
folder (recursively), analyzes each track's **tempo (BPM)**, **musical key** (with Camelot
wheel code for harmonic mixing), and **energy**, then generates ordered playlists for a
target set length or track count, with an embedded player, live statistics, and export
options. Modern dark/light UI.

Created by **J. Baudru (Bonoob)**.

## Features

- Recursive folder scan (`mp3`, `wav`, `flac`, `ogg`), analyzed in parallel with a thread pool
- Automatic metadata: tempo, key (Camelot code), energy (1-10 normalized), genre (from tags,
  with a tempo-based fallback heuristic when no tag is present)
- Tracks appear in the library live as each one finishes analyzing (no waiting for the full scan)
- Playlist generation modes:
  - **Fixed tempo** — consistent tempo band, ordered for harmonic (Camelot) compatibility
  - **Tempo progression** — build up (or down) tempo across the set
  - **Fixed energy** — consistent energy band
  - **Energy progression** — energy build across the set
- Target set length (30 min, 1h, 2h, ...) with duration-aware track selection
- Filter by genre / tempo range / energy range
- Embedded audio player (play/pause/seek/volume)
- Live stats: tempo curve, energy curve, genre breakdown, totals
- Export: save as `.m3u` / `.m3u8` (importable in Rekordbox and similar DJ software), or copy
  files in playlist order into a folder with numeric prefixes (`01 - Artist - Title.mp3`, ...),
  or save the playlist definition to the library
- Local JSON library cache (no external database) — re-scanning only re-analyzes new/changed files

## Dependencies

Kept intentionally minimal for a small, portable build:

- `PySide6` — UI, embedded player (QtMultimedia)
- `numpy` — all tempo/key/energy DSP (STFT, onset detection, autocorrelation, chroma)
- `miniaudio` — lightweight audio decoding (mp3/wav/flac/ogg), no scipy/numba/llvmlite
- `mutagen` — tag reading

No matplotlib, scipy, numba, or SQL database — stats are drawn with plain `QPainter`, and
the library is plain JSON.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Building a Windows .exe

```powershell
pip install pyinstaller
pyinstaller build.spec
```

The executable is produced in `dist/Setuno.exe` (single file, no console window).

## Building on macOS (later)

The codebase is pure Python/Qt (PySide6) with no Windows-specific APIs, so the same
`pyinstaller build.spec` command works on macOS to produce a `.app` bundle — just run it
from a Mac with the dependencies installed.

## Notes

- The library is stored as plain JSON files under `data/library.json` and `data/playlists.json`,
  next to `main.py` (or next to `Setuno.exe` when built). No external database.
- Analysis of long tracks is capped to a ~90s representative window for speed; this keeps
  scanning fast while still giving reliable tempo/key/energy estimates.
- Genre is read from existing ID3/Vorbis tags when present; otherwise a simple tempo-based
  heuristic label is assigned (can be corrected by tagging files with a proper genre).

## Roadmap — ideas for later

Useful DJ/radio/curator features not yet implemented, kept here as a backlog:

- **Beat-grid & waveform view** — visual waveform with detected beat markers/downbeats for precise manual mixing cues.
- **Cue points & hot cues** — save/recall intro, drop, and outro markers per track.
- **Auto-crossfade preview** — simulate the transition between two tracks (tempo-matched crossfade) directly in the embedded player.
- **Key-lock / pitch preview** — preview a track at an adjusted BPM without changing pitch.
- **Duplicate detection** — flag same-song duplicates (different files/bitrates) in the library.
- **Vocal/instrumental detection** — tag tracks as vocal, instrumental, or acapella for smarter mixing.
- **Smart re-shuffle** — regenerate just a portion of a playlist (e.g. the last 10 tracks) without rebuilding the whole set.
- **Set history / play log** — track what was actually played (and when) across gigs, for reporting or PRO/royalty logging (useful for radio).
- **Tag editor** — edit title/artist/genre/BPM/key directly from the library table and write changes back to file tags.
- **Streaming service import** — import a Spotify/SoundCloud/Bandcamp playlist as a reference to match against the local library.
- **BPM/key manual override & confidence score** — let the user correct auto-detected values, and show a confidence indicator for low-certainty detections.
- **Multiple output profiles** — export presets per target software (Rekordbox, Serato, Traktor, generic M3U) with their specific quirks/metadata.
- **Energy curve templates** — named curve presets (e.g. "warm-up", "peak-time", "afters") that drive the energy-progression generator automatically.
- **Multi-folder libraries** — manage several independent watched folders/crates instead of one flat library.
- **Cloud/network drive support** — tolerate slow or intermittently available paths (NAS, external drives) without blocking scans.
