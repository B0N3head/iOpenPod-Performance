from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from SyncEngine.sync_executor import SyncExecutor


def test_auto_write_workers_use_hdd_safe_default_for_classic(tmp_path: Path) -> None:
    executor = SyncExecutor(
        tmp_path,
        max_workers=6,
        max_device_write_workers=0,
        device_info=SimpleNamespace(model_family="iPod Classic", generation="6th Gen"),
    )

    assert executor._max_workers == 6
    assert executor._max_device_write_workers == 1


def test_auto_write_workers_use_flash_friendly_default_for_nano(tmp_path: Path) -> None:
    executor = SyncExecutor(
        tmp_path,
        max_workers=6,
        max_device_write_workers=0,
        device_info=SimpleNamespace(model_family="iPod Nano", generation="7th Gen"),
    )

    assert executor._max_device_write_workers == 4


def test_explicit_write_workers_override_auto_and_clamp_to_overall(tmp_path: Path) -> None:
    executor = SyncExecutor(
        tmp_path,
        max_workers=2,
        max_device_write_workers=4,
        device_info=SimpleNamespace(model_family="iPod Classic", generation="6th Gen"),
    )

    assert executor._max_device_write_workers == 2


def test_auto_write_workers_preserve_existing_behavior_without_device_info(
    tmp_path: Path,
) -> None:
    executor = SyncExecutor(
        tmp_path,
        max_workers=5,
        max_device_write_workers=0,
        device_info=None,
    )

    assert executor._max_device_write_workers == 5


def test_device_write_limit_serializes_final_ipod_writes(monkeypatch, tmp_path: Path) -> None:
    ipod_root = tmp_path / "ipod"
    source = tmp_path / "source.mp3"
    ipod_root.mkdir()
    source.write_bytes(b"source-bytes")

    executor = SyncExecutor(
        ipod_root,
        max_workers=4,
        max_device_write_workers=1,
    )

    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_copy_file_chunked(src, dst, progress=None, chunk_size=256 * 1024, is_cancelled=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            Path(dst).write_bytes(Path(src).read_bytes())
            if progress:
                progress(1.0)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(executor, "_copy_file_chunked", fake_copy_file_chunked)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(executor._copy_to_ipod, source, False)
            for _ in range(4)
        ]
        results = [future.result() for future in futures]

    assert all(success for success, _path, _was_transcoded, _err in results)
    assert max_active == 1


def test_device_write_limit_allows_multiple_parallel_writes_when_configured(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ipod_root = tmp_path / "ipod"
    source = tmp_path / "source.mp3"
    ipod_root.mkdir()
    source.write_bytes(b"source-bytes")

    executor = SyncExecutor(
        ipod_root,
        max_workers=4,
        max_device_write_workers=2,
    )

    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_copy_file_chunked(src, dst, progress=None, chunk_size=256 * 1024, is_cancelled=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            Path(dst).write_bytes(Path(src).read_bytes())
            if progress:
                progress(1.0)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(executor, "_copy_file_chunked", fake_copy_file_chunked)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(executor._copy_to_ipod, source, False)
            for _ in range(4)
        ]
        results = [future.result() for future in futures]

    assert all(success for success, _path, _was_transcoded, _err in results)
    assert 1 < max_active <= 2
