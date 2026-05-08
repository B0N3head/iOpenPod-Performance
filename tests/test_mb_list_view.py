from GUI.widgets.MBListView import build_new_regular_playlist


def test_build_new_regular_playlist_marks_payload_as_new_regular_playlist() -> None:
    playlist = build_new_regular_playlist(
        [
            {"track_id": 101, "Title": "First"},
            {"track_id": 202, "Title": "Second"},
        ]
    )

    assert playlist is not None
    assert playlist["Title"] == "New Playlist"
    assert playlist["_isNew"] is True
    assert playlist["_source"] == "regular"
    assert isinstance(playlist["playlist_id"], int)
    assert playlist["playlist_id"] > 0
    assert playlist["items"] == [{"track_id": 101}, {"track_id": 202}]


def test_build_new_regular_playlist_returns_none_without_valid_track_ids() -> None:
    assert build_new_regular_playlist([{"Title": "Missing ID"}, {"track_id": 0}]) is None
