"""Cross-platform safe-eject helper for iPods.

Provides a single entry point, :func:`eject_ipod`, that unmounts and
(where applicable) powers down the device behind a given mount path.

Strategies per platform:
  * **Windows** — Shell.Application "Eject" verb via PowerShell.
  * **macOS**   — ``diskutil eject``.
  * **Linux**   — ``udisksctl unmount`` + ``udisksctl power-off`` first,
                  then ``eject``, then plain ``umount`` as fallbacks.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT_SECS = 30


def eject_ipod(mount_path: str) -> tuple[bool, str]:
    """Safely eject / unmount an iPod at *mount_path*.

    Returns ``(success, message)``.  The *message* is suitable for
    display in a dialog or log entry.
    """
    if not mount_path:
        return False, "No device path supplied."

    path = Path(mount_path)
    try:
        if sys.platform == "win32":
            return _eject_windows(path)
        if sys.platform == "darwin":
            return _eject_macos(path)
        return _eject_linux(path)
    except Exception as exc:  # last-ditch safety net
        logger.exception("eject_ipod: unexpected failure")
        return False, f"Unexpected error: {exc}"


# ──────────────────────────────────────────────────────────────────────
# Windows
# ──────────────────────────────────────────────────────────────────────

def _eject_windows(path: Path) -> tuple[bool, str]:
    """Invoke the shell's "Eject" verb on the drive letter.

    Works for standard removable iPod volumes without requiring admin
    privileges.  We escape the drive letter into a single-quoted
    PowerShell string; only ``'`` needs special handling, and drive
    letters never contain one.
    """
    drive = path.drive  # e.g. "E:" for "E:\\iPod_Control\\..."
    if not drive:
        return False, f"Cannot determine drive letter from {path}."

    ps_cmd = (
        "$ErrorActionPreference = 'Stop'; "
        "$shell = New-Object -ComObject Shell.Application; "
        f"$shell.Namespace(17).ParseName('{drive}').InvokeVerb('Eject')"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return False, "PowerShell is not available on this system."
    except subprocess.TimeoutExpired:
        return False, "Eject timed out."

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or f"PowerShell exited with code {proc.returncode}."
    return True, f"Ejected {drive}"


# ──────────────────────────────────────────────────────────────────────
# macOS
# ──────────────────────────────────────────────────────────────────────

def _eject_macos(path: Path) -> tuple[bool, str]:
    """Eject via ``diskutil eject <mount_path>``."""
    try:
        proc = subprocess.run(
            ["diskutil", "eject", str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECS,
        )
    except FileNotFoundError:
        return False, "diskutil is not available."
    except subprocess.TimeoutExpired:
        return False, "Eject timed out."

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or "diskutil eject failed."
    return True, f"Ejected {path}"


# ──────────────────────────────────────────────────────────────────────
# Linux
# ──────────────────────────────────────────────────────────────────────

def _eject_linux(path: Path) -> tuple[bool, str]:
    """Try udisksctl, then ``eject``, then ``umount``, in that order."""
    device = _find_block_device(str(path))

    if device and shutil.which("udisksctl"):
        ok, msg = _udisks_eject(device)
        if ok:
            return True, msg
        logger.debug("udisksctl eject failed, falling back: %s", msg)

    if shutil.which("eject"):
        try:
            proc = subprocess.run(
                ["eject", str(path)],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            return False, "eject timed out."
        if proc.returncode == 0:
            return True, f"Ejected {path}"
        logger.debug("eject command failed: %s", (proc.stderr or proc.stdout).strip())

    if shutil.which("umount"):
        try:
            proc = subprocess.run(
                ["umount", str(path)],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            return False, "umount timed out."
        if proc.returncode == 0:
            return True, f"Unmounted {path}"
        err = (proc.stderr or proc.stdout).strip()
        return False, err or "umount failed."

    return False, "No suitable unmount utility found (tried udisksctl, eject, umount)."


def _find_block_device(mount_path: str) -> str | None:
    """Return the block device backing *mount_path* (e.g. ``/dev/sdb1``)."""
    if shutil.which("findmnt"):
        try:
            proc = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", "--target", mount_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                source = proc.stdout.strip()
                if source:
                    return source
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Fallback: scan /proc/mounts for the longest matching mountpoint.
    try:
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
            best: str | None = None
            best_len = -1
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                dev, mp = parts[0], parts[1].replace(r"\040", " ")
                if mount_path == mp or mount_path.startswith(mp.rstrip("/") + "/"):
                    if len(mp) > best_len:
                        best, best_len = dev, len(mp)
            return best
    except OSError:
        return None


def _udisks_eject(device: str) -> tuple[bool, str]:
    """Unmount then power off the parent disk via ``udisksctl``."""
    try:
        u = subprocess.run(
            [
                "udisksctl", "unmount",
                "--block-device", device,
                "--no-user-interaction",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return False, "udisksctl unmount timed out."

    if u.returncode != 0:
        err = (u.stderr or u.stdout).strip()
        if "not mounted" not in err.lower():
            return False, err or "udisksctl unmount failed."

    parent = _parent_block_device(device)
    if parent:
        try:
            subprocess.run(
                [
                    "udisksctl", "power-off",
                    "--block-device", parent,
                    "--no-user-interaction",
                ],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            pass  # unmount succeeded; power-off is best-effort

    return True, f"Ejected {device}"


def _parent_block_device(device: str) -> str | None:
    """Given ``/dev/sdb1`` return ``/dev/sdb`` (``nvme0n1p1`` → ``nvme0n1``)."""
    name = device.rsplit("/", 1)[-1]
    m = re.match(r"^(nvme\d+n\d+)p\d+$", name)
    if m:
        return "/dev/" + m.group(1)
    m = re.match(r"^(mmcblk\d+)p\d+$", name)
    if m:
        return "/dev/" + m.group(1)
    m = re.match(r"^([a-z]+)\d+$", name)
    if m:
        return "/dev/" + m.group(1)
    return None
