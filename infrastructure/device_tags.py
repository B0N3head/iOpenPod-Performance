"""Persisted per-device tags keyed by iPod identity (serial/firewire)."""

from __future__ import annotations

import json
import os
from typing import Any

from .settings_paths import get_settings_dir
from .settings_secrets import (
    normalized_device_identity_value,
    normalized_device_mount_key,
)

_TAGS_FILENAME = "device_tags.json"


def _tags_path() -> str:
    return os.path.join(get_settings_dir(), _TAGS_FILENAME)


def _device_tag_key(device_info: Any | None, ipod_root: str = "") -> str:
    if device_info is not None:
        for attr in (
            "serial",
            "serial_number",
            "firewire_guid",
            "usb_serial",
            "vpd_serial",
        ):
            value = normalized_device_identity_value(getattr(device_info, attr, ""))
            if value:
                return value
    return normalized_device_mount_key(ipod_root)


def _load_tags() -> dict[str, dict[str, Any]]:
    path = _tags_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}

    if isinstance(raw, dict):
        if "devices" in raw and isinstance(raw.get("devices"), dict):
            return dict(raw.get("devices") or {})
        return dict(raw)
    return {}


def _save_tags(tags: dict[str, dict[str, Any]]) -> None:
    path = _tags_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"version": 1, "devices": tags}
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def get_ipod_hdd_tag(device_info: Any | None, ipod_root: str = "") -> bool | None:
    key = _device_tag_key(device_info, ipod_root)
    if not key:
        return None
    tags = _load_tags()
    entry = tags.get(key)
    if not isinstance(entry, dict):
        return None
    value = entry.get("ipod_hdd")
    return value if isinstance(value, bool) else None


def set_ipod_hdd_tag(
    device_info: Any | None,
    ipod_root: str,
    ipod_hdd: bool,
    *,
    device_name: str = "",
    serial: str = "",
) -> None:
    key = _device_tag_key(device_info, ipod_root)
    if not key:
        return

    # Derive name/serial from device_info if not supplied explicitly.
    if not device_name and device_info is not None:
        device_name = str(getattr(device_info, "display_name", "") or "").strip()
    if not serial and device_info is not None:
        for attr in ("serial", "serial_number", "firewire_guid", "usb_serial", "vpd_serial"):
            v = str(getattr(device_info, attr, "") or "").strip()
            if v:
                serial = v
                break

    tags = _load_tags()
    existing = tags.get(key) or {}
    tags[key] = {
        **existing,
        "ipod_hdd": bool(ipod_hdd),
        "device_name": device_name or existing.get("device_name", ""),
        "serial": serial or existing.get("serial", ""),
    }
    _save_tags(tags)


def list_all_device_tags() -> list[dict]:
    """Return all stored device entries for display in settings UI."""
    tags = _load_tags()
    result = []
    for key, entry in tags.items():
        if not isinstance(entry, dict):
            continue
        result.append({
            "key": key,
            "device_name": entry.get("device_name", "") or key,
            "serial": entry.get("serial", ""),
            "ipod_hdd": entry.get("ipod_hdd"),
        })
    return result
