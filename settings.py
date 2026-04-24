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
import base64
import copy
import hashlib
from dataclasses import dataclass, asdict, field
from importlib.metadata import version as _pkg_version
from typing import Optional


DEVICE_SETTINGS_RELATIVE = os.path.join("iPod_Control", "iOpenPod", "settings.json")
DEVICE_SETTING_KEYS = (
    "write_back_to_pc",
    "compute_sound_check",
    "rotate_tall_photos_for_device",
    "fit_photo_thumbnails",
    "rating_conflict_strategy",
    "aac_encoder",
    "aac_mode",
    "aac_music_bitrate",
    "aac_vbr_level",
    "aac_spoken_bitrate",
    "video_crf",
    "video_preset",
    "prefer_lossy",
    "sync_workers",
    "normalize_sample_rate",
    "mono_for_spoken",
    "smart_quality_by_type",
    "show_art_in_tracklist",
    "accent_color",
    "scrobble_on_sync",
    "listenbrainz_token",
    "listenbrainz_username",
    "backup_before_sync",
)
DEVICE_SECRET_KEYS = {"listenbrainz_token"}


def _normalized_device_mount_key(ipod_root: str) -> str:
    if not ipod_root:
        return ""
    return os.path.normcase(os.path.abspath(ipod_root))


def _normalized_device_identity_value(value) -> str:
    return str(value or "").replace(" ", "").strip().upper()


def get_version() -> str:
    """Return the app version from pyproject.toml metadata."""
    try:
        return _pkg_version("iopenpod")
    except Exception:
        return "1.0.46"


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

    # When enabled, portrait-heavy photos can be rotated clockwise on the
    # device's viewing caches when that makes better use of the iPod's
    # landscape photo screen.
    rotate_tall_photos_for_device: bool = False

    # Controls thumbnail rendering in the iPod Photos database pipeline.
    # False (default): iTunes-style crop-to-fill thumbnails.
    # True: aspect-fit thumbnails with letterboxing/padding.
    fit_photo_thumbnails: bool = False

    # Rating conflict strategy when iPod and PC ratings differ.
    # Options: ipod_wins, pc_wins, highest, lowest, average.
    rating_conflict_strategy: str = "ipod_wins"

    # ── External Tools ────────────────────────────────────────────────────
    # Custom path to ffmpeg binary. Empty = auto-detect (bundled → system PATH).
    ffmpeg_path: str = ""

    # Custom path to fpcalc binary. Empty = auto-detect (bundled → system PATH).
    fpcalc_path: str = ""

    # ── Transcoding ─────────────────────────────────────────────────────────

    # AAC Encoder Configuration
    # Which encoder to use: "auto" picks best available (libfdk_aac > aac_at > aac).
    aac_encoder: str = "auto"

    # Encoding mode. Allowed values depend on encoder:
    #   libfdk_aac: "cbr" (recommended), "vbr"
    #   aac_at:     "cbr", "cvbr" (recommended), "abr", "vbr"
    #   aac:        "cbr" only
    aac_mode: str = "cbr"

    # Target bitrate in kbps for CBR/CVBR/ABR music encodes.
    # Sensible range: 64–256. Maximum useful quality for AAC is at 256 kbps.
    aac_music_bitrate: int = 192

    # VBR quality level for libfdk_aac VBR mode (1 = lowest, 5 = highest).
    # Level 5 can spike above 256 kbps — may cause instability on pre-Classic iPods.
    aac_vbr_level: int = 4

    # Bitrate for spoken-word (podcast/audiobook) encodes. Always CBR.
    aac_spoken_bitrate: int = 64

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

    # Automatically use spoken-word bitrate for files whose media type
    # is Podcast, Audiobook, or iTunes U (stik atom values 1, 2, 21).
    # Music files always use the configured music bitrate.
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

        # The transcoder caches a few settings-derived decisions. Invalidate
        # them immediately so new transcoding plans use the freshly-saved
        # values without requiring an app restart.
        try:
            from SyncEngine.transcoder import clear_caches as _clear_transcoder_caches
            _clear_transcoder_caches()
        except Exception:
            pass
        try:
            refresh_effective_settings()
        except Exception:
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

            # ── Migration: music_folder (str) → media_folder (str) ─────
            if "media_folder" not in data and "music_folder" in data:
                settings.media_folder = data["music_folder"]

            # ── Migration: aac_bitrate (int) → new encoder config fields ──
            if "aac_bitrate" in data and "aac_music_bitrate" not in data:
                _br = int(data["aac_bitrate"])
                if _br == 64:
                    settings.aac_spoken_bitrate = 64
                else:
                    settings.aac_music_bitrate = min(_br, 256)

            # ── Migration: aac_quality (str) → new encoder config fields ──
            if "aac_quality" in data and "aac_music_bitrate" not in data:
                _old_q = data.get("aac_quality", "normal")
                _q_map = {"high": 256, "normal": 192, "compact": 128}
                if _old_q == "spoken":
                    settings.aac_spoken_bitrate = 64
                else:
                    settings.aac_music_bitrate = _q_map.get(_old_q, 192)

        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
        return settings


@dataclass
class DeviceSettingsState:
    """Loaded on-iPod settings plus metadata for the Settings page."""

    settings: AppSettings
    use_global_settings: bool = False
    exists: bool = False
    path: str = ""


def _copy_settings(settings: AppSettings) -> AppSettings:
    """Return a detached copy of settings, including mutable fields."""
    return copy.deepcopy(settings)


def _copy_device_settings_state(state: DeviceSettingsState) -> DeviceSettingsState:
    """Return a detached copy of loaded device settings state."""
    return DeviceSettingsState(
        settings=_copy_settings(state.settings),
        use_global_settings=bool(state.use_global_settings),
        exists=bool(state.exists),
        path=state.path,
    )


def _coerce_setting_value(current_value, value):
    expected_type = type(current_value)
    if expected_type is bool:
        return value if isinstance(value, bool) else None
    if expected_type is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None
    if expected_type is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None
    if expected_type is list:
        return value if isinstance(value, list) else None
    return value if isinstance(value, expected_type) else None


def _apply_settings_values(settings: AppSettings, data: dict, allowed_keys) -> None:
    for key in allowed_keys:
        if key not in data or not hasattr(settings, key):
            continue
        coerced = _coerce_setting_value(getattr(settings, key), data[key])
        if coerced is not None:
            setattr(settings, key, coerced)


def device_settings_path(ipod_root: str) -> str:
    return os.path.join(ipod_root, DEVICE_SETTINGS_RELATIVE)


def has_device_settings(ipod_root: str) -> bool:
    return bool(ipod_root) and os.path.exists(device_settings_path(ipod_root))


def device_settings_key(ipod_root: str = "", device_info=None) -> str:
    """Build a stable-ish key for lightly obfuscating on-device secrets."""
    candidates = []
    if device_info is not None:
        for attr in ("firewire_guid", "serial", "serial_number", "model_number"):
            value = _normalized_device_identity_value(getattr(device_info, attr, ""))
            if value:
                candidates.append(value)
    if candidates:
        return "|".join(candidates)

    mount_key = _normalized_device_mount_key(ipod_root)
    if mount_key:
        return mount_key
    return "unknown-device"


def _device_key_candidates(
    device_key: str = "",
    ipod_root: str = "",
    stored_hint: str = "",
) -> list[str]:
    mount_key = _normalized_device_mount_key(ipod_root)
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        candidate = str(candidate or "").strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    _add(stored_hint)
    _add(device_key)

    for candidate in tuple(candidates):
        if mount_key and candidate not in {mount_key, "unknown-device"}:
            # Backward compatibility for the earlier branch behavior that
            # mixed stable device identity with the current mount path.
            _add(f"{candidate}|{mount_key}")

    _add(mount_key)
    if not candidates:
        _add("unknown-device")
    return candidates


def _secret_key(device_key: str, nonce: bytes = b"") -> bytes:
    seed = f"iOpenPod device settings v1|{device_key or 'unknown-device'}".encode("utf-8")
    return hashlib.sha256(seed + nonce).digest()


def _xor_stream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _encrypt_secret(value: str, device_key: str) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8")
    nonce = os.urandom(16)
    key = _secret_key(device_key, nonce)
    stream = _xor_stream(key, len(raw))
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    return "xor1:{nonce}:{cipher}".format(
        nonce=base64.urlsafe_b64encode(nonce).decode("ascii"),
        cipher=base64.urlsafe_b64encode(cipher).decode("ascii"),
    )


def _decrypt_secret(value: str, device_key: str) -> str:
    if not value or not isinstance(value, str):
        return ""
    if not value.startswith("xor1:"):
        return value
    try:
        _prefix, nonce_b64, cipher_b64 = value.split(":", 2)
        nonce = base64.urlsafe_b64decode(nonce_b64.encode("ascii"))
        cipher = base64.urlsafe_b64decode(cipher_b64.encode("ascii"))
        key = _secret_key(device_key, nonce)
        stream = _xor_stream(key, len(cipher))
        raw = bytes(a ^ b for a, b in zip(cipher, stream))
        return raw.decode("utf-8")
    except Exception:
        return ""


def _decrypt_secret_for_device(
    value: str,
    *,
    device_key: str,
    ipod_root: str = "",
    stored_hint: str = "",
) -> str:
    if not value or not isinstance(value, str):
        return ""
    if not value.startswith("xor1:"):
        return value

    for candidate in _device_key_candidates(
        device_key=device_key,
        ipod_root=ipod_root,
        stored_hint=stored_hint,
    ):
        decrypted = _decrypt_secret(value, candidate)
        if decrypted:
            return decrypted
    return ""


def _serialized_device_settings(settings: AppSettings, device_key: str) -> dict:
    data = {}
    for key in DEVICE_SETTING_KEYS:
        value = getattr(settings, key)
        if key in DEVICE_SECRET_KEYS:
            value = _encrypt_secret(value, device_key)
        data[key] = value
    return data


def _clear_transcoder_caches() -> None:
    try:
        from SyncEngine.transcoder import clear_caches as _clear_caches
        _clear_caches()
    except Exception:
        pass


def _load_device_settings_unlocked(
    ipod_root: str,
    device_key: str = "",
    base_settings: AppSettings | None = None,
) -> DeviceSettingsState:
    base = _copy_settings(base_settings or _get_global_settings_unlocked())
    path = device_settings_path(ipod_root)
    if not ipod_root or not os.path.exists(path):
        return DeviceSettingsState(settings=base, exists=False, path=path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return DeviceSettingsState(settings=base, exists=True, path=path)

    if not isinstance(raw, dict):
        return DeviceSettingsState(settings=base, exists=True, path=path)

    use_global = bool(raw.get("use_global_settings", False))
    data = raw.get("settings", raw)
    if not isinstance(data, dict):
        data = {}
    stored_key_hint = str(raw.get("device_key_hint", "") or "")

    decoded = dict(data)
    for key in DEVICE_SECRET_KEYS:
        if key in decoded and isinstance(decoded[key], str):
            decoded[key] = _decrypt_secret_for_device(
                decoded[key],
                device_key=device_key,
                ipod_root=ipod_root,
                stored_hint=stored_key_hint,
            )

    _apply_settings_values(base, decoded, DEVICE_SETTING_KEYS)
    return DeviceSettingsState(
        settings=base,
        use_global_settings=use_global,
        exists=True,
        path=path,
    )


def load_device_settings(
    ipod_root: str,
    device_key: str = "",
    base_settings: AppSettings | None = None,
) -> DeviceSettingsState:
    if base_settings is None:
        with _settings_lock:
            base_settings = _copy_settings(_get_global_settings_unlocked())
    return _load_device_settings_unlocked(ipod_root, device_key, base_settings)


def get_device_settings_for_edit(
    ipod_root: str,
    device_key: str = "",
) -> DeviceSettingsState:
    """Load device settings, or initialize an unsaved edit copy from globals."""
    active_state = get_active_device_settings_state(ipod_root, device_key)
    if active_state is not None:
        return active_state
    return load_device_settings(ipod_root, device_key, get_global_settings())


def save_device_settings(
    ipod_root: str,
    settings: AppSettings,
    use_global_settings: bool = False,
    device_key: str = "",
) -> None:
    global _effective_instance, _active_device_state
    global _active_device_root, _active_device_key, _active_device_use_global
    path = device_settings_path(ipod_root)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    payload = {
        "version": 1,
        "use_global_settings": bool(use_global_settings),
        "settings": _serialized_device_settings(settings, device_key),
    }
    if device_key and device_key != "unknown-device":
        payload["device_key_hint"] = device_key
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    _clear_transcoder_caches()
    with _settings_lock:
        if _active_device_root and os.path.normcase(os.path.abspath(_active_device_root)) == os.path.normcase(os.path.abspath(ipod_root)):
            state = DeviceSettingsState(
                settings=_copy_settings(settings),
                use_global_settings=bool(use_global_settings),
                exists=True,
                path=path,
            )
            global_settings = _get_global_settings_unlocked()
            _active_device_root = ipod_root or ""
            _active_device_key = device_key or ""
            _active_device_use_global = bool(use_global_settings)
            _active_device_state = state
            _effective_instance = (
                global_settings if use_global_settings else state.settings
            )


def reset_device_settings_to_global(
    ipod_root: str,
    device_key: str = "",
    use_global_settings: bool = False,
) -> AppSettings:
    """Replace the on-iPod settings file with current global device settings."""
    settings = _copy_settings(get_global_settings())
    save_device_settings(
        ipod_root,
        settings,
        use_global_settings=use_global_settings,
        device_key=device_key,
    )
    return settings


# ── Singleton accessor ──────────────────────────────────────────────────────

_global_instance: Optional[AppSettings] = None
_effective_instance: Optional[AppSettings] = None
_active_device_state: Optional[DeviceSettingsState] = None
_active_device_root: str = ""
_active_device_key: str = ""
_active_device_use_global: bool = False
_settings_lock = threading.RLock()


def _get_global_settings_unlocked() -> AppSettings:
    global _global_instance, _effective_instance
    if _global_instance is None:
        _global_instance = AppSettings.load()
    if _effective_instance is None:
        _effective_instance = _global_instance
    return _global_instance


def get_global_settings() -> AppSettings:
    """Get the PC/global settings instance."""
    with _settings_lock:
        return _get_global_settings_unlocked()


def _activate_device_settings_unlocked(
    ipod_root: str,
    device_key: str = "",
) -> DeviceSettingsState:
    global _effective_instance, _active_device_state
    global _active_device_root, _active_device_key, _active_device_use_global
    global_settings = _get_global_settings_unlocked()
    state = _load_device_settings_unlocked(ipod_root, device_key, global_settings)
    _active_device_root = ipod_root or ""
    _active_device_key = device_key or ""
    _active_device_use_global = bool(state.use_global_settings)
    _active_device_state = _copy_device_settings_state(state)
    _effective_instance = global_settings if (not state.exists or state.use_global_settings) else state.settings
    return state


def apply_loaded_device_settings(
    ipod_root: str,
    device_key: str,
    state: DeviceSettingsState,
) -> AppSettings:
    """Activate a device-settings state that was loaded off the UI thread."""
    global _effective_instance, _active_device_state
    global _active_device_root, _active_device_key, _active_device_use_global
    with _settings_lock:
        global_settings = _get_global_settings_unlocked()
        state_copy = _copy_device_settings_state(state)
        _active_device_root = ipod_root or ""
        _active_device_key = device_key or ""
        _active_device_use_global = bool(state_copy.use_global_settings)
        _active_device_state = state_copy
        _effective_instance = (
            global_settings
            if (not state_copy.exists or state_copy.use_global_settings)
            else state_copy.settings
        )
        return _effective_instance


def activate_device_settings(ipod_root: str, device_key: str = "") -> DeviceSettingsState:
    """Activate on-device settings for the selected iPod, if present."""
    with _settings_lock:
        return _activate_device_settings_unlocked(ipod_root, device_key)


def clear_device_settings() -> AppSettings:
    """Return to the global settings profile."""
    global _effective_instance, _active_device_state
    global _active_device_root, _active_device_key, _active_device_use_global
    with _settings_lock:
        _active_device_root = ""
        _active_device_key = ""
        _active_device_use_global = False
        _active_device_state = None
        _effective_instance = _get_global_settings_unlocked()
        return _effective_instance


def get_active_device_settings_state(
    ipod_root: str = "",
    device_key: str = "",
) -> DeviceSettingsState | None:
    """Return the active device settings state without reading the iPod."""
    with _settings_lock:
        if _active_device_state is None or not _active_device_root:
            return None
        if ipod_root:
            active_root = os.path.normcase(os.path.abspath(_active_device_root))
            requested_root = os.path.normcase(os.path.abspath(ipod_root))
            if active_root != requested_root:
                return None
        if device_key and device_key != _active_device_key:
            return None
        return _copy_device_settings_state(_active_device_state)


def refresh_effective_settings() -> AppSettings:
    """Rebuild the effective settings after global settings were saved."""
    global _effective_instance, _active_device_state
    with _settings_lock:
        global_settings = _get_global_settings_unlocked()
        if _active_device_root:
            state = _active_device_state
            if state is not None and state.exists and not state.use_global_settings:
                refreshed = _copy_settings(global_settings)
                for key in DEVICE_SETTING_KEYS:
                    if hasattr(refreshed, key) and hasattr(state.settings, key):
                        setattr(refreshed, key, getattr(state.settings, key))
                _active_device_state = DeviceSettingsState(
                    settings=refreshed,
                    use_global_settings=state.use_global_settings,
                    exists=state.exists,
                    path=state.path,
                )
                _effective_instance = refreshed
                effective = refreshed
            else:
                _effective_instance = global_settings
                effective = global_settings
        else:
            _effective_instance = global_settings
            effective = global_settings
        assert effective is not None
        return effective


def get_settings() -> AppSettings:
    """Get settings currently effective for the selected device."""
    global _effective_instance
    with _settings_lock:
        if _effective_instance is None:
            _effective_instance = _get_global_settings_unlocked()
        return _effective_instance


def reload_settings() -> AppSettings:
    """Force reload from disk, preserving the active device overlay."""
    global _global_instance, _effective_instance
    with _settings_lock:
        _global_instance = AppSettings.load()
        if _active_device_root:
            _activate_device_settings_unlocked(_active_device_root, _active_device_key)
            effective = _effective_instance
        else:
            effective = _global_instance
            _effective_instance = effective
    assert effective is not None
    return effective
