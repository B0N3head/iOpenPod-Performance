import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from infrastructure import settings_persistence
from infrastructure.settings_persistence import load_app_settings, save_app_settings
from infrastructure.settings_schema import AppSettings


@contextmanager
def repo_temp_dir():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / ".tmp" / f"settings-persistence-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_settings_persistence_round_trip(monkeypatch) -> None:
    with repo_temp_dir() as tmp_path:
        settings_dir = tmp_path / "settings"
        monkeypatch.setattr(
            settings_persistence,
            "default_settings_dir",
            lambda: str(settings_dir),
        )
        monkeypatch.setattr(
            settings_persistence,
            "get_settings_path",
            lambda: str(settings_dir / "settings.json"),
        )

        settings = AppSettings(media_folder="C:/Music", window_width=1440)
        save_app_settings(settings)

        loaded = load_app_settings()

    assert loaded.media_folder == "C:/Music"
    assert loaded.window_width == 1440


def test_settings_persistence_migrates_legacy_music_folder(monkeypatch) -> None:
    with repo_temp_dir() as tmp_path:
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        path = settings_dir / "settings.json"
        path.write_text(json.dumps({"music_folder": "C:/OldMusic"}), encoding="utf-8")
        monkeypatch.setattr(
            settings_persistence,
            "get_settings_path",
            lambda: str(path),
        )

        loaded = load_app_settings()

    assert loaded.media_folder == "C:/OldMusic"
