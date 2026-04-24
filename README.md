# iOpenPod

**Ditch iTunes. Sync your iPod the open way.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Win | Mac | Linux](https://img.shields.io/badge/Platform-Win%20%7C%20Mac%20%7C%20Linux-lightgrey.svg)](#download)
[![GitHub Release](https://img.shields.io/github/v/release/TheRealSavi/iOpenPod)](https://github.com/TheRealSavi/iOpenPod/releases/latest)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/9Yy499Tf5d)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/johngibbons)

iOpenPod is a free, open-source desktop app that lets you manage your iPod without iTunes. Plug in your iPod, and sync. FLAC, OGG, MP3, any file format, it automatically handles the conversion. Access to all the features your iPod makes possible. Works on Windows, macOS, and Linux.

![Album Browser](assets/screenshots/hero.png)

---

## Download

Grab the latest release for your platform. No Python required for the native builds, no setup wizards, just download, extract, and run:

### ➡️ [Download iOpenPod here](https://github.com/TheRealSavi/iOpenPod/releases/latest)

| Platform | File | Instructions |
|----------|------|-------------|
| **Windows** | `iOpenPod-windows.zip` | Extract, and run `iOpenPod.exe` |
| **macOS** | `iOpenPod-macos.tar.gz` | Extract, and run `iOpenPod.app`, you will need to allow the unknown developer in System Settings. |
| **Linux (All distros, recommended)** | `iOpenPod-Linux-x86_64.AppImage ` | Make it executable with `chmod a+x ./iOpenPod-Linux-x86_64.AppImage` and run. |
| **Linux (Arch-based)** | [`iopenpod`](https://aur.archlinux.org/packages/iopenpod)<sup>AUR</sup> | Available in the AUR |
| **Linux (All distros)** | `iOpenPod-linux.tar.gz` | Extract, and run `./iOpenPod` |

Once installed, iOpenPod can check for updates automatically and can update itself right from the app. (Except when installed from AUR or a Python package manager.)

### Install from PyPI

If you prefer a Python package install, iOpenPod is also available through `pip`, `pipx`, and `uv tool`. These options expose the same `iopenpod` command.

| Method | Install | Run | Upgrade |
|--------|---------|-----|---------|
| `pip` | `python -m pip install iopenpod` | `iopenpod` | `python -m pip install --upgrade iopenpod` |
| `pipx` | `pipx install iopenpod` | `iopenpod` | `pipx upgrade iopenpod` |
| `uv tool` | `uv tool install iopenpod` | `iopenpod` | `uv tool upgrade iopenpod` |

Requires **Python 3.11+**.

If `iopenpod` is not on your shell `PATH` yet, run `pipx ensurepath` for `pipx` installs or `uv tool update-shell` for `uv tool` installs.

Package-manager installs should be updated with the same tool you used to install them, not with the in-app binary updater.

> **Optional extras:** Install [FFmpeg](https://ffmpeg.org/) for transcoding (FLAC to ALAC, etc.) and [Chromaprint](https://acoustid.org/chromaprint) for acoustic fingerprinting needed for syncing.

---

## How to Use

1. **Plug in your iPod** — Make sure it is mounted as a drive
2. **Pick your device** — Select your iPod in iOpenPod. If it detects the iPod incorrectly, please open an issue.
3. **Browse** — Manage your iPod's library, modifying any existing tracks, playlists, podcasts, etc.
4. **Sync** — Press sync, choose a folder on your PC to sync, decide what you want and what you don't, and you're done.

![Device Picker](assets/screenshots/devicepicker.png)

---

## Features

### 🎵 Sync Music From Any Format
Drop in any file format and iOpenPod transcodes whatever the iPod can't play natively into ALAC or AAC. Converted files are cached so repeat syncs are fast.

### 📻 Podcasts
Subscribe to podcasts right inside iOpenPod with the built-in podcast manager. Search, subscribe, download episodes, and sync them to your iPod.

![Podcasts](assets/screenshots/podcasts.png)

### 🎧 ListenBrainz Scrobbling
Sign into ListenBrainz and your listening history gets scrobbled automatically every time you sync.

### 📚 More Than Just Music
Audiobooks, movies, and TV shows are all supported. iOpenPod handles the different media types so your iPod sorts them correctly.

### 🖱️ Drag and Drop
Don't care about keeping your PC and iPod perfectly in sync? Just drag files into the app and they'll land on your iPod. No fingerprinting or file tracking.

![Manage Tracks](assets/screenshots/managetracks.png)

### 📊 Play Counts & Ratings
Listen on your iPod, plug it in, and your play counts, ratings, and skip counts can sync back to your PC library.

### 🖼️ Album Art Just Works
Art gets extracted from your files, resized, and written in the iPod's native RGB565 format. No extra steps.

### ✅ Review Before You Commit
Every sync shows you exactly what's happening, adds, removes, metadata updates, all with checkboxes for each item. Nothing changes until you say so.

![Sync Review](assets/screenshots/syncreview.png)

### 📋 Playlists & Smart Playlists
Browse and manage standard playlists. Smart playlists with rule-based filtering are supported too.

![Playlists](assets/screenshots/playlists.png)
![Smart Playlists](assets/screenshots/smartplaylist.png)

### 🛡️ Backup & Rollback
A snapshot of your iPod database is saved before every sync. If something goes wrong, roll back to any previous state.

![Backups](assets/screenshots/backup.png)

### ⚙️ Configurable
Tweak transcoding settings, sync behavior, and more.

---

## Supported iPods

Works with every click-wheel iPod Apple ever made. Shuffle support coming soon!

| Device | Status | Notes |
|--------|--------|-------|
| iPod 1G–5G, Mini, Photo | ✅ Fully supported | No hash required |
| iPod Classic (all gens) | ✅ Fully supported | Uses FireWire ID |
| iPod Nano 1G–2G | ✅ Fully supported | No hash required |
| iPod Nano 3G–4G | ✅ Fully supported | Uses FireWire ID |
| iPod Nano 5G | ✅ Fully supported | Needs one iTunes sync for HashInfo |
| iPod Nano 6G–7G | ✅ Fully supported | HASHAB via WebAssembly |
| iPod Shuffle | 🔜 Coming soon | |
| iPod Touch | ❌ Not planned | |

---

## For Developers

Want to help make iOpenPod? Here's how to get a dev environment running.

### Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **[FFmpeg](https://ffmpeg.org/)** (for transcoding)
- **[Chromaprint](https://acoustid.org/chromaprint)** (for fingerprinting)

### Setup

```bash
git clone https://github.com/TheRealSavi/iOpenPod.git
cd iOpenPod
uv sync
uv run python main.py
```

That's it. `uv sync` installs all dependencies into a virtual environment automatically.

### Project Layout

```
iOpenPod/
├── GUI/                    # PyQt6 interface
│   ├── app.py              # Main window
│   └── widgets/            # Album grid, track list, sidebar, sync review, etc.
├── iTunesDB_Parser/        # Reads iPod's binary iTunesDB
├── iTunesDB_Writer/        # Writes iTunesDB
├── ArtworkDB_Parser/       # Reads ArtworkDB binary format
├── ArtworkDB_Writer/       # Writes album art to .ithmb files
├── SyncEngine/             # Fingerprinting, diffing, transcoding, sync execution
├── PodcastManager/         # Podcast search, subscription, and download
├── SQLiteDB_Writer/        # SQLite DB for Nano 6G/7G
└── main.py                 # Entry point
```

### How Sync Works

The sync engine matches tracks between your PC and iPod using acoustic fingerprints ([Chromaprint](https://acoustid.org/chromaprint)). This means it can identify the same song even after re-encoding, format conversion, or metadata changes.

1. Scan both the PC media folder and iPod's iTunesDB
2. Compute or read cached fingerprints for each track
3. Diff by fingerprint to classify: new, removed, changed, or matched
4. Present the sync plan for review
5. Copy/transcode files, update the database, sync artwork and play counts
6. Rebuild the iTunesDB binary with the correct device-specific checksum

### Areas Where Help Is Needed

- **Real hardware testing** - Every iPod is a little different.
- **macOS and Linux testing** - Primary dev is on Windows
- **Bug reports** - Open an issue with steps to reproduce

Please open an issue before starting major changes so we can coordinate, or [join the discord server](https://discord.gg/9Yy499Tf5d).

### Related Projects

- [libgpod](https://github.com/gtkpod/libgpod) — C library for iPod database access (the reference implementation this project learned from)
- [gtkpod](https://github.com/gtkpod/gtkpod) — GTK+ iPod manager
- [Rockbox](https://www.rockbox.org/) — Open-source firmware replacement for iPods

---

## Support

iOpenPod is and always will be completely free and open source. If you like it and would like to support me, it is so very appreciated.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/johngibbons)

## License

MIT — see [LICENSE](LICENSE).
