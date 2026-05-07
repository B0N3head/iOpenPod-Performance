from __future__ import annotations

from pathlib import Path

from GUI.widgets.podcastBrowser import (
    _is_remote_artwork_source,
    _read_local_artwork_bytes,
    _resolve_local_artwork_path,
)


def test_http_artwork_source_is_remote() -> None:
    assert _is_remote_artwork_source("https://example.com/cover.jpg") is True
    assert _is_remote_artwork_source("http://example.com/cover.jpg") is True
    assert _is_remote_artwork_source(r"G:\iPod_Control\cover.jpg") is False


def test_read_local_artwork_bytes_reads_existing_file(tmp_path: Path) -> None:
    image_path = tmp_path / "cover.jpg"
    image_path.write_bytes(b"image-bytes")

    assert _read_local_artwork_bytes(str(image_path)) == b"image-bytes"


def test_read_local_artwork_bytes_treats_missing_windows_path_as_local() -> None:
    missing = r"G:\iPod_Control\iOpenPodPodcasts\artwork-cache\cover.jpg"

    assert _resolve_local_artwork_path(missing) == Path(missing)
    assert _read_local_artwork_bytes(missing) == b""


def test_read_local_artwork_bytes_supports_file_uri(tmp_path: Path) -> None:
    image_path = tmp_path / "artwork cache" / "cover.jpg"
    image_path.parent.mkdir()
    image_path.write_bytes(b"uri-bytes")

    uri = image_path.as_uri()

    assert _read_local_artwork_bytes(uri) == b"uri-bytes"
