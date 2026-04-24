"""JSON persistence for global application settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from .settings_paths import (
    default_settings_dir,
    get_settings_path,
)
from .settings_schema import AppSettings


def save_app_settings(settings: AppSettings) -> None:
    """Write settings to the active settings directory."""

    active_dir = settings.settings_dir or default_settings_dir()
    os.makedirs(active_dir, exist_ok=True)

    path = os.path.join(active_dir, "settings.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(asdict(settings), file, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    default_dir = default_settings_dir()
    if settings.settings_dir and settings.settings_dir != default_dir:
        write_settings_redirect(default_dir, settings.settings_dir)


def write_settings_redirect(default_dir: str, custom_dir: str) -> None:
    """Write a minimal redirect file at the default settings location."""

    os.makedirs(default_dir, exist_ok=True)
    redirect = os.path.join(default_dir, "settings.json")
    try:
        with open(redirect, "w", encoding="utf-8") as file:
            json.dump({"settings_dir": custom_dir}, file, indent=2)
    except OSError:
        pass


def load_app_settings() -> AppSettings:
    """Load settings from JSON, returning defaults for missing keys."""

    path = get_settings_path()
    settings = AppSettings()
    if not os.path.exists(path):
        return settings
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return settings

        for key, value in data.items():
            if hasattr(settings, key):
                expected_type = type(getattr(settings, key))
                if isinstance(value, int) and expected_type is float:
                    value = float(value)
                if isinstance(value, expected_type):
                    setattr(settings, key, value)

        if "media_folder" not in data and "music_folder" in data:
            settings.media_folder = data["music_folder"]

        if "aac_bitrate" in data and "aac_music_bitrate" not in data:
            bitrate = int(data["aac_bitrate"])
            if bitrate == 64:
                settings.aac_spoken_bitrate = 64
            else:
                settings.aac_music_bitrate = min(bitrate, 256)

        if "aac_quality" in data and "aac_music_bitrate" not in data:
            old_quality = data.get("aac_quality", "normal")
            quality_map = {"high": 256, "normal": 192, "compact": 128}
            if old_quality == "spoken":
                settings.aac_spoken_bitrate = 64
            else:
                settings.aac_music_bitrate = quality_map.get(old_quality, 192)

    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        pass
    return settings
