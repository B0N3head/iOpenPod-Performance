from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from SyncEngine.playlist_parser import parse_playlist, resolve_existing_playlist_path


def test_parse_playlist_decodes_local_file_uri(tmp_path: Path) -> None:
    track = tmp_path / "Artist Name" / "Track 01.mp3"
    track.parent.mkdir()
    track.write_bytes(b"audio")

    playlist = tmp_path / "road_trip.m3u8"
    playlist.write_text(f"file://localhost{quote(str(track))}\n", encoding="utf-8")

    paths, playlist_name = parse_playlist(playlist)

    assert paths == [str(track)]
    assert playlist_name == "Road Trip"


def test_resolve_existing_playlist_path_handles_remote_file_uri(tmp_path: Path) -> None:
    track = tmp_path / "Network Share" / "Track 02.mp3"
    track.parent.mkdir()
    track.write_bytes(b"audio")

    resolved = resolve_existing_playlist_path(f"file://nas{quote(str(track))}")

    assert resolved == str(track)


def test_resolve_existing_playlist_path_normalizes_backslashes_on_posix(
    tmp_path: Path,
) -> None:
    track = tmp_path / "Library" / "Track 03.mp3"
    track.parent.mkdir()
    track.write_bytes(b"audio")

    raw_path = str(track)
    if os.name != "nt":
        raw_path = raw_path.replace("/", "\\")

    resolved = resolve_existing_playlist_path(raw_path)

    expected = str(track) if os.name != "nt" else raw_path
    assert resolved == expected
