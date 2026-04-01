"""
Application settings with JSON persistence.

Settings are stored in the platform-appropriate directory:
  Windows: %APPDATA%/iOpenPod/settings.json
  macOS:   ~/Library/Application Support/iOpenPod/settings.json
  Linux:   $XDG_CONFIG_HOME/iOpenPod/settings.json  (~/.config/iOpenPod/)

User data (logs, backups) follows the same convention:
  Windows: ~/iOpenPod/
  macOS:   ~/Library/Application Support/iOpenPod/
  Linux:   $XDG_DATA_HOME/iOpenPod/  (~/.local/share/iOpenPod/)

Cache data (transcoded files):
  Windows: ~/iOpenPod/cache/
  macOS:   ~/Library/Caches/iOpenPod/
  Linux:   $XDG_CACHE_HOME/iOpenPod/  (~/.cache/iOpenPod/)

On Unix, if the legacy ~/iOpenPod directory exists it is used instead
so existing installs keep working.

The default location always acts as a bootstrap: if it contains a
``settings_dir`` override, the real settings are loaded/saved from
that directory instead.  A small redirect file is kept at the default
location so the next launch can find the custom path.
"""

import json
import threading
import os
import sys
from dataclasses import dataclass, asdict, field
from importlib.metadata import version as _pkg_version
from typing import Optional


def get_version() -> str:
    """Return the app version from pyproject.toml metadata."""
    try:
        return _pkg_version("iopenpod")
    except Exception:
        return "1.0.30"


def default_data_dir() -> str:
    """Base directory for iOpenPod user data (logs, backups).

    On Linux, follows XDG Base Directory Specification:
        $XDG_DATA_HOME/iOpenPod  (default: ~/.local/share/iOpenPod)
    On Windows: ~/iOpenPod
    On macOS:   ~/Library/Application Support/iOpenPod

    If the legacy ~/iOpenPod directory exists on a Unix system it is used
    instead, so existing installs keep working until the user moves it.
    """
    home = os.path.expanduser("~")
    legacy = os.path.join(home, "iOpenPod")

    if sys.platform == "win32":
        return legacy
    elif sys.platform == "darwin":
        xdg = os.path.join(home, "Library", "Application Support", "iOpenPod")
        return legacy if os.path.isdir(legacy) else xdg
    else:
        # Linux / other Unix — XDG_DATA_HOME
        if os.path.isdir(legacy):
            return legacy
        base = os.environ.get(
            "XDG_DATA_HOME", os.path.join(home, ".local", "share"),
        )
        return os.path.join(base, "iOpenPod")


def default_cache_dir() -> str:
    """Base directory for iOpenPod cache data (transcode cache).

    On Linux: $XDG_CACHE_HOME/iOpenPod  (default: ~/.cache/iOpenPod)
    On Windows: ~/iOpenPod/cache
    On macOS:   ~/Library/Caches/iOpenPod

    If the legacy ~/iOpenPod/cache directory exists on a Unix system it
    is used instead.
    """
    home = os.path.expanduser("~")
    legacy = os.path.join(home, "iOpenPod", "cache")

    if sys.platform == "win32":
        return legacy
    elif sys.platform == "darwin":
        xdg = os.path.join(home, "Library", "Caches", "iOpenPod")
        return legacy if os.path.isdir(legacy) else xdg
    else:
        if os.path.isdir(legacy):
            return legacy
        base = os.environ.get(
            "XDG_CACHE_HOME", os.path.join(home, ".cache"),
        )
        return os.path.join(base, "iOpenPod")


def _default_settings_dir() -> str:
    """Get the platform-appropriate *default* settings directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "iOpenPod")


def get_settings_dir() -> str:
    """
    Resolve the active settings directory.

    Checks the default location for a ``settings_dir`` redirect.  If the
    redirect points to a valid directory, that directory is used.  Otherwise
    the default is used.
    """
    default_dir = _default_settings_dir()
    redirect_path = os.path.join(default_dir, "settings.json")

    if os.path.exists(redirect_path):
        try:
            with open(redirect_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            custom = data.get("settings_dir", "")
            if custom and os.path.isdir(custom) and custom != default_dir:
                # Verify the custom location actually has (or can have) a settings file
                return custom
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass

    return default_dir


def _get_settings_path() -> str:
    return os.path.join(get_settings_dir(), "settings.json")


@dataclass
class AppSettings:
    """All user-configurable settings."""

    # ── Paths ───────────────────────────────────────────────────────────────
    # Custom settings directory (empty = platform default).
    # Changing this moves settings storage to the new location.
    settings_dir: str = ""

    # Custom transcode cache directory (empty = platform default via default_cache_dir).
    transcode_cache_dir: str = ""

    # Maximum transcode cache size in gigabytes.  0.0 = unlimited.
    # When a new file would push the cache over this limit, the least-recently-
    # used entries are evicted first.
    max_cache_size_gb: float = 5.0

    # Custom log directory (empty = platform default via default_data_dir/logs).
    # Covers both app logs and crash reports.
    log_dir: str = ""

    # Custom backup directory (empty = platform default via default_data_dir/backups).
    backup_dir: str = ""

    # ── Sync ────────────────────────────────────────────────────────────────
    # Default PC media folder for sync (remembered between sessions)
    media_folder: str = ""

    # Write ratings back to PC source files after sync.
    # Off by default — users must opt in to having source files modified.
    write_back_to_pc: bool = False

    # Compute Sound Check (loudness normalization) for files that don't
    # already have ReplayGain or iTunNORM tags. Uses ffmpeg's EBU R128
    # measurement and writes the result back into the PC file's tags.
    # Sound Check values are always synced to iPod regardless of this setting.
    compute_sound_check: bool = False

    # Rating conflict strategy when iPod and PC ratings differ.
    # Options: ipod_wins, pc_wins, highest, lowest, average.
    rating_conflict_strategy: str = "ipod_wins"

    # ── External Tools ────────────────────────────────────────────────────
    # Custom path to ffmpeg binary. Empty = auto-detect (bundled → system PATH).
    ffmpeg_path: str = ""

    # Custom path to fpcalc binary. Empty = auto-detect (bundled → system PATH).
    fpcalc_path: str = ""

    # ── Transcoding ─────────────────────────────────────────────────────────
    # AAC quality preset for lossy transcodes (OGG/Opus/WMA → AAC).
    # Uses encoder-specific flags (VBR for libfdk_aac, CVBR for aac_at, CBR for aac).
    # Options: "high", "normal", "compact", "spoken".
    aac_quality: str = "normal"

    # Video quality (CRF) for H.264 transcodes. Lower = better quality.
    # 18=high, 20=good, 23=balanced, 26=low, 28=very low.
    video_crf: int = 23

    # x264 encode speed preset for video transcodes.
    # Slower presets produce better quality at the same CRF.
    video_preset: str = "fast"

    # When True, lossless sources (FLAC/WAV/AIFF) are encoded to AAC
    # instead of ALAC, saving space at the cost of quality.
    prefer_lossy: bool = False

    # Number of parallel transcode/copy workers.
    # 0 = auto (CPU count), 1 = sequential (legacy behaviour).
    sync_workers: int = 0

    # Always resample audio output to 44.1 kHz (CD rate).
    # Default False preserves the source sample rate (capped at 48 kHz).
    # Enable for maximum compatibility with early iPod models that can have
    # quirks with 48 kHz PCM inside ALAC, and to shrink high-res (96 kHz)
    # FLAC transcodes.
    normalize_sample_rate: bool = False

    # When AAC quality is "spoken" (64 kbps), downmix stereo to mono.
    # Stereo at 64 kbps = ~32 kbps per channel.  Mono at 64 kbps sounds
    # significantly better and cuts file size by ~50%.
    # Only affects spoken-word transcodes; music tracks are unchanged.
    mono_for_spoken: bool = True

    # Automatically use "spoken" AAC quality for files whose media type
    # is Podcast, Audiobook, or iTunes U (stik atom values 1, 2, 21).
    # Music files always use the configured aac_quality preset.
    smart_quality_by_type: bool = True

    # ── Library ─────────────────────────────────────────────────────────────
    # Last selected iPod device path (remembered between sessions)
    last_device_path: str = ""

    # ── Appearance ──────────────────────────────────────────────────────────
    # Show album art in the track list view
    show_art_in_tracklist: bool = True

    # Theme: "dark", "light", or "system" (follow OS preference).
    theme: str = "dark"
    # Increased contrast: "off", "on", or "system" (follow OS accessibility).
    high_contrast: str = "off"
    # Font scale factor: "75%", "90%", "100%", "110%", "125%", "150%".
    font_scale: str = "100%"

    # Accent color: "blue" (default theme accent), "match-ipod" (use the
    # connected iPod's body color), or a hex string like "#e34060".
    accent_color: str = "blue"

    # Remembered window dimensions (not exposed in settings UI).
    window_width: int = 1280
    window_height: int = 720

    # Remembered splitter sizes for grid/track split (not exposed in UI).
    # Empty list = use default 60/40 split.
    splitter_sizes: list = field(default_factory=list)

    # ── Scrobbling ──────────────────────────────────────────────────────────
    # Submit iPod play counts to ListenBrainz after each sync.
    scrobble_on_sync: bool = True

    # ListenBrainz user token (copied from listenbrainz.org/settings).
    # Empty = disabled.
    listenbrainz_token: str = ""

    # ListenBrainz username (stored for display, populated on token validation).
    listenbrainz_username: str = ""

    # ── Backups ─────────────────────────────────────────────────────────────
    # Automatically create a full device backup before each sync.
    backup_before_sync: bool = True

    # Maximum number of backup snapshots to retain per device (0 = unlimited).
    max_backups: int = 10

    def save(self) -> None:
        """Write settings to the active settings directory.

        If ``settings_dir`` is set, settings are written there **and** a
        small redirect file is kept at the default location so the next
        launch can find the custom path.
        """
        active_dir = self.settings_dir or _default_settings_dir()
        os.makedirs(active_dir, exist_ok=True)

        path = os.path.join(active_dir, "settings.json")
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

        # Keep a redirect at the default location when using a custom dir
        default_dir = _default_settings_dir()
        if self.settings_dir and self.settings_dir != default_dir:
            self._write_redirect(default_dir, self.settings_dir)
        elif not self.settings_dir:
            # Using the default — the normal save above overwrites any
            # stale redirect, so nothing extra to do.
            pass

    @staticmethod
    def _write_redirect(default_dir: str, custom_dir: str) -> None:
        """Write a minimal redirect file at the default location."""
        os.makedirs(default_dir, exist_ok=True)
        redirect = os.path.join(default_dir, "settings.json")
        try:
            with open(redirect, "w", encoding="utf-8") as f:
                json.dump({"settings_dir": custom_dir}, f, indent=2)
        except OSError:
            pass

    @classmethod
    def load(cls) -> "AppSettings":
        """Load settings from JSON, returning defaults for missing keys."""
        path = _get_settings_path()
        settings = cls()
        if not os.path.exists(path):
            return settings
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return settings
            # Only set known fields — silently ignore unknown keys
            for key, value in data.items():
                if hasattr(settings, key):
                    expected_type = type(getattr(settings, key))
                    # Allow int values for float fields (common in JSON serialization)
                    if isinstance(value, int) and expected_type is float:
                        value = float(value)
                    if isinstance(value, expected_type):
                        setattr(settings, key, value)

            # ── Migration: aac_bitrate (int) → aac_quality (str) ────────
            if "aac_quality" not in data and "aac_bitrate" in data:
                _br = data["aac_bitrate"]
                _map = {64: "spoken", 128: "compact", 192: "normal",
                        256: "normal", 320: "high"}
                settings.aac_quality = _map.get(_br, "normal")

        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
        return settings


# ── Singleton accessor ──────────────────────────────────────────────────────

_instance: Optional[AppSettings] = None
_settings_lock = threading.Lock()


def get_settings() -> AppSettings:
    """Get the global settings instance (loaded once on first access)."""
    global _instance
    if _instance is None:
        with _settings_lock:
            if _instance is None:
                _instance = AppSettings.load()
    return _instance


def reload_settings() -> AppSettings:
    """Force reload from disk."""
    global _instance
    with _settings_lock:
        _instance = AppSettings.load()
    return _instance
