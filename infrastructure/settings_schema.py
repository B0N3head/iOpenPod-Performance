"""Settings data contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

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


@dataclass
class AppSettings:
    """All user-configurable settings."""

    settings_dir: str = ""
    transcode_cache_dir: str = ""
    max_cache_size_gb: float = 5.0
    log_dir: str = ""
    backup_dir: str = ""

    media_folder: str = ""
    write_back_to_pc: bool = False
    compute_sound_check: bool = False
    rotate_tall_photos_for_device: bool = False
    fit_photo_thumbnails: bool = False
    rating_conflict_strategy: str = "ipod_wins"

    ffmpeg_path: str = ""
    fpcalc_path: str = ""

    aac_encoder: str = "auto"
    aac_mode: str = "cbr"
    aac_music_bitrate: int = 192
    aac_vbr_level: int = 4
    aac_spoken_bitrate: int = 64
    video_crf: int = 23
    video_preset: str = "fast"
    prefer_lossy: bool = False
    sync_workers: int = 0
    normalize_sample_rate: bool = False
    mono_for_spoken: bool = True
    smart_quality_by_type: bool = True

    last_device_path: str = ""

    show_art_in_tracklist: bool = True
    theme: str = "dark"
    high_contrast: str = "off"
    font_scale: str = "100%"
    accent_color: str = "blue"
    window_width: int = 1280
    window_height: int = 720
    splitter_sizes: list = field(default_factory=list)

    scrobble_on_sync: bool = True
    listenbrainz_token: str = ""
    listenbrainz_username: str = ""

    backup_before_sync: bool = True
    max_backups: int = 10


@dataclass
class DeviceSettingsState:
    """Loaded on-iPod settings plus metadata for the Settings page."""

    settings: AppSettings
    use_global_settings: bool = False
    exists: bool = False
    path: str = ""
