from __future__ import annotations

from typing import cast

from PyQt6.QtCore import QObject

from app_core import controllers
from app_core.controllers import QuickWriteController
from app_core.jobs import QuickPlaylistSyncWorker
from app_core.services import DeviceManagerLike, LibraryCacheLike


class _FakeCache:
    def __init__(self) -> None:
        self.committed = 0
        self._pending = [{"playlist_id": 123, "Title": "Pending"}]

    def commit_user_playlists(self) -> None:
        self.committed += 1

    def has_pending_playlists(self) -> bool:
        return bool(self._pending)

    def get_user_playlists(self) -> list[dict]:
        return list(self._pending)


class _FakeDeviceManager:
    def __init__(self) -> None:
        self.device_path = "/fake/ipod"


class _FakeWorker(QObject):
    def wait(self) -> bool:
        return True

    def deleteLater(self) -> None:
        pass


def test_quick_playlist_done_commits_pending_playlists_after_success() -> None:
    cache = _FakeCache()
    controller = QuickWriteController(
        device_manager=cast(DeviceManagerLike, _FakeDeviceManager()),
        library_cache=cast(LibraryCacheLike, cache),
        is_sync_running=lambda: False,
    )
    controller._playlist_worker = cast(QuickPlaylistSyncWorker, _FakeWorker())

    class _Result:
        success = True
        errors = []

    controller._on_playlist_done(_Result())

    assert cache.committed == 1
    assert controller._playlist_worker is None


def test_start_playlist_sync_does_not_clear_pending_before_success(monkeypatch) -> None:
    created: dict[str, object] = {}

    class _FakeSignal:
        def connect(self, _callback) -> None:
            pass

    class _FakePlaylistWorker:
        def __init__(self, ipod_path: str, user_playlists: list[dict], on_complete=None) -> None:
            created["ipod_path"] = ipod_path
            created["user_playlists"] = user_playlists
            created["on_complete"] = on_complete
            self.completed = _FakeSignal()
            self.error = _FakeSignal()

        def start(self) -> None:
            created["started"] = True

    monkeypatch.setattr(controllers, "QuickPlaylistSyncWorker", _FakePlaylistWorker)

    cache = _FakeCache()
    controller = QuickWriteController(
        device_manager=cast(DeviceManagerLike, _FakeDeviceManager()),
        library_cache=cast(LibraryCacheLike, cache),
        is_sync_running=lambda: False,
    )

    controller.start_playlist_sync()

    assert created["ipod_path"] == "/fake/ipod"
    assert created["user_playlists"] == [{"playlist_id": 123, "Title": "Pending"}]
    assert created["on_complete"] is None
    assert created["started"] is True
