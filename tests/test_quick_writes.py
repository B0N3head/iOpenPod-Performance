from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from SyncEngine import quick_writes


@dataclass
class FakePlaylistInfo:
    playlist_id: int
    track_ids: list[int]


def test_write_user_playlist_replaces_target_and_merges_pending(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    tracks_data = [{"track_id": 10, "db_track_id": 100}]
    all_tracks = [object()]

    def fake_load(_ipod_path):
        return (
            tracks_data,
            [{"playlist_id": 1, "Title": "Existing"}],
            [{"playlist_id": 2, "Title": "Old Smart"}],
            all_tracks,
        )

    def fake_evaluate(**kwargs):
        captured.update(kwargs)
        return (
            "iPod",
            [FakePlaylistInfo(1, [100]), FakePlaylistInfo(3, [100])],
            [FakePlaylistInfo(2, [100, 101])],
        )

    def fake_write(*args, **kwargs):
        captured["write_args"] = args
        captured["write"] = kwargs
        return True

    monkeypatch.setattr(quick_writes, "_load_database_state", fake_load)
    monkeypatch.setattr(quick_writes, "_evaluate_tracks_and_playlists", fake_evaluate)
    monkeypatch.setattr(quick_writes, "_write_evaluated_database", fake_write)

    result = quick_writes.write_user_playlist(
        "I:/",
        {"playlist_id": 2, "Title": "New Smart"},
        [{"playlist_id": 3, "Title": "Pending", "_isNew": True}],
    )

    assert result.success
    assert result.playlist_name == "New Smart"
    assert result.matched_count == 2
    assert captured["smart_raw"] == [{"playlist_id": 2, "Title": "New Smart"}]
    assert captured["playlists_raw"] == [
        {"playlist_id": 1, "Title": "Existing"},
        {"playlist_id": 3, "Title": "Pending", "_isNew": True},
    ]
    assert captured["write"]["master_playlist_name"] == "iPod"


def test_write_imported_playlist_converts_db_track_ids_to_track_ids(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_load(_ipod_path):
        return (
            [
                {"track_id": 10, "db_track_id": 100},
                {"track_id": 20, "db_id": 200},
            ],
            [],
            [],
            [object()],
        )

    def fake_evaluate(**kwargs):
        captured.update(kwargs)
        return ("iPod", [FakePlaylistInfo(55, [100, 200])], [])

    def fake_write(*args, **kwargs):
        captured["write_args"] = args
        captured["write"] = kwargs
        return True

    monkeypatch.setattr(quick_writes, "_load_database_state", fake_load)
    monkeypatch.setattr(quick_writes, "_evaluate_tracks_and_playlists", fake_evaluate)
    monkeypatch.setattr(quick_writes, "_write_evaluated_database", fake_write)

    result = quick_writes.write_imported_playlist_from_db_track_ids(
        "I:/",
        "Imported",
        [100, 999, 200],
        [],
        playlist_id=55,
    )

    assert result.success
    assert result.playlist_name == "Imported"
    assert result.matched_count == 2
    assert captured["playlists_raw"] == [
        {
            "Title": "Imported",
            "playlist_id": 55,
            "_isNew": True,
            "_source": "regular",
            "items": [{"track_id": 10}, {"track_id": 20}],
        }
    ]
