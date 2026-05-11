from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ArtworkDB_Writer import artwork_writer as aw


def _make_ipod_root(tmp_path: Path) -> tuple[Path, Path]:
    ipod_root = tmp_path / "ipod"
    artwork_dir = ipod_root / "iPod_Control" / "Artwork"
    artwork_dir.mkdir(parents=True)
    return ipod_root, artwork_dir


def _make_track(db_track_id: int, *, hint: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        db_track_id=db_track_id,
        title=f"Track {db_track_id}",
        album="Album",
        artist="Artist",
        album_artist="Artist",
        mhii_link=0,
        artwork_count=0,
        artwork_size=0,
        _iop_artwork_sync_hint=hint,
    )


def _existing_art_entry(ithmb_path: Path, *, song_id: int = 1) -> dict[int, dict]:
    return {
        42: {
            "song_id": song_id,
            "src_img_size": 99,
            "formats": {
                100: {
                    "path": str(ithmb_path),
                    "ithmb_offset": 0,
                    "size": 4,
                    "width": 1,
                    "height": 1,
                    "hpad": 0,
                    "vpad": 0,
                },
            },
        },
    }


def test_write_artworkdb_preserves_unchanged_art_without_reencoding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ipod_root, artwork_dir = _make_ipod_root(tmp_path)
    pc_file = tmp_path / "song.mp3"
    pc_file.write_bytes(b"music")
    existing_ithmb = artwork_dir / "F100_1.ithmb"
    existing_ithmb.write_bytes(b"OLD!")

    monkeypatch.setattr(aw, "_read_existing_artwork", lambda *_args, **_kwargs: _existing_art_entry(existing_ithmb))
    monkeypatch.setattr(aw, "get_artwork_format_definitions", lambda _ipod_path: {})
    monkeypatch.setattr(aw, "expected_size_bytes", lambda *_args, **_kwargs: 4)

    def _fail_extract(_path: str) -> bytes | None:
        raise AssertionError("unchanged artwork should use the preserve fast-path")

    monkeypatch.setattr(aw, "extract_art_with_folder", _fail_extract)

    result = aw.write_artworkdb(
        str(ipod_root),
        [_make_track(1, hint="preserve_existing")],
        pc_file_paths={1: str(pc_file)},
        artwork_formats={100: (1, 1)},
    )

    assert result[1] == (100, 99)
    assert existing_ithmb.read_bytes() == b"OLD!"


def test_write_artworkdb_clears_removed_art_instead_of_preserving(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ipod_root, artwork_dir = _make_ipod_root(tmp_path)
    pc_file = tmp_path / "song.mp3"
    pc_file.write_bytes(b"music")
    existing_ithmb = artwork_dir / "F100_1.ithmb"
    existing_ithmb.write_bytes(b"OLD!")

    monkeypatch.setattr(aw, "_read_existing_artwork", lambda *_args, **_kwargs: _existing_art_entry(existing_ithmb))
    monkeypatch.setattr(aw, "get_artwork_format_definitions", lambda _ipod_path: {})
    monkeypatch.setattr(aw, "expected_size_bytes", lambda *_args, **_kwargs: 4)
    monkeypatch.setattr(aw, "extract_art_with_folder", lambda _path: None)

    result = aw.write_artworkdb(
        str(ipod_root),
        [_make_track(1)],
        pc_file_paths={1: str(pc_file)},
        artwork_formats={100: (1, 1)},
    )

    assert result == {}
    assert not existing_ithmb.exists()


def test_write_artworkdb_preserves_existing_art_when_source_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ipod_root, artwork_dir = _make_ipod_root(tmp_path)
    missing_pc_file = tmp_path / "missing.mp3"
    existing_ithmb = artwork_dir / "F100_1.ithmb"
    existing_ithmb.write_bytes(b"OLD!")

    monkeypatch.setattr(aw, "_read_existing_artwork", lambda *_args, **_kwargs: _existing_art_entry(existing_ithmb))
    monkeypatch.setattr(aw, "get_artwork_format_definitions", lambda _ipod_path: {})
    monkeypatch.setattr(aw, "expected_size_bytes", lambda *_args, **_kwargs: 4)

    result = aw.write_artworkdb(
        str(ipod_root),
        [_make_track(1)],
        pc_file_paths={1: str(missing_pc_file)},
        artwork_formats={100: (1, 1)},
    )

    assert result[1] == (100, 99)
    assert existing_ithmb.exists()
    assert existing_ithmb.read_bytes() == b"OLD!"


def test_write_artworkdb_zero_art_case_removes_stale_ithmbs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ipod_root, artwork_dir = _make_ipod_root(tmp_path)
    stale_ithmb = artwork_dir / "F100_1.ithmb"
    stale_ithmb.write_bytes(b"OLD!")

    monkeypatch.setattr(aw, "_read_existing_artwork", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(aw, "get_artwork_format_definitions", lambda _ipod_path: {})

    result = aw.write_artworkdb(
        str(ipod_root),
        [],
        pc_file_paths={},
        artwork_formats={100: (1, 1)},
    )

    assert result == {}
    assert not stale_ithmb.exists()
    assert (artwork_dir / "ArtworkDB").exists()
