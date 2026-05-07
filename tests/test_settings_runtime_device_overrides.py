from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from infrastructure import settings_runtime
from infrastructure.settings_runtime import SettingsRuntime
from infrastructure.settings_schema import AppSettings


@contextmanager
def repo_temp_dir():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / ".tmp" / f"settings-runtime-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_device_settings_round_trip_preserves_device_write_workers(monkeypatch) -> None:
    with repo_temp_dir() as tmp_path:
        monkeypatch.setattr(settings_runtime, "_clear_transcoder_caches", lambda: None)
        runtime = SettingsRuntime()

        device_settings = AppSettings(
            sync_workers=6,
            device_write_workers=1,
            media_folder="C:/Music",
        )
        runtime.save_device_settings(
            str(tmp_path),
            device_settings,
            device_key="SERIAL123",
        )

        loaded = runtime.load_device_settings(
            str(tmp_path),
            "SERIAL123",
            AppSettings(),
        )

    assert loaded.settings.sync_workers == 6
    assert loaded.settings.device_write_workers == 1
