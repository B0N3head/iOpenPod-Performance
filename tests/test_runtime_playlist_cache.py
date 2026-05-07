from __future__ import annotations

from types import SimpleNamespace

from app_core import runtime


def test_commit_user_playlists_hydrates_pending_playlist_into_live_cache(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime.DeviceManager,
        "get_instance",
        classmethod(lambda cls: SimpleNamespace(device_path="/fake/ipod")),
    )

    cache = runtime.iTunesDBCache()
    cache.set_data(
        {
            "mhlt": [],
            "mhlp": [
                {
                    "playlist_id": 1,
                    "Title": "Existing",
                    "items": [{"track_id": 7}],
                    "mhip_child_count": 1,
                }
            ],
            "mhlp_podcast": [],
            "mhlp_smart": [],
        },
        "/fake/ipod",
    )

    cache.save_user_playlist(
        {
            "playlist_id": 2,
            "Title": "New Playlist",
            "_source": "regular",
            "items": [{"track_id": 10}, {"track_id": 20}],
        }
    )

    cache.commit_user_playlists()

    assert cache.has_pending_playlists() is False

    playlists = sorted(
        cache.get_playlists(),
        key=lambda playlist: int(playlist.get("playlist_id", 0) or 0),
    )

    assert [playlist["playlist_id"] for playlist in playlists] == [1, 2]
    assert playlists[1]["Title"] == "New Playlist"
    assert playlists[1]["items"] == [{"track_id": 10}, {"track_id": 20}]
    assert playlists[1]["mhip_child_count"] == 2
