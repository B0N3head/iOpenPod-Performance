from dataclasses import FrozenInstanceError

import pytest

from app_core.services import SettingsSnapshot
from infrastructure.settings_schema import AppSettings


def test_settings_snapshot_copies_values_and_freezes_lists() -> None:
    settings = AppSettings(
        media_folder="C:/Music",
        theme="light",
        accent_color="#123456",
        device_write_workers=2,
        splitter_sizes=[300, 700],
        window_width=1440,
        window_height=900,
    )

    snapshot = SettingsSnapshot.from_settings(settings)

    assert snapshot.media_folder == "C:/Music"
    assert snapshot.theme == "light"
    assert snapshot.accent_color == "#123456"
    assert snapshot.device_write_workers == 2
    assert snapshot.splitter_sizes == (300, 700)
    assert snapshot.window_width == 1440
    assert snapshot.window_height == 900

    with pytest.raises(FrozenInstanceError):
        snapshot.theme = "dark"  # type: ignore[misc]
