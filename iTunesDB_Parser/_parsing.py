"""
Internal parsing helpers shared across iTunesDB chunk parsers.

Provides:
- Pre-compiled ``struct.Struct`` objects for common binary field widths.
- :func:`read_generic_header` — reads the 12-byte generic chunk header.

Child-iteration helpers live in :mod:`chunk_parser` to avoid circular
imports (they need ``parse_chunk``, which dispatches back to the typed
parsers that use this module's struct helpers).
"""

from __future__ import annotations

import struct
from typing import Any

from .exceptions import CorruptHeaderError, InsufficientDataError

# ── Pre-compiled struct objects ──────────────────────────────────────
# Used by callers (e.g. mhod_parser) that do inline struct reads.
# The Shared defs module still uses ad-hoc struct.unpack calls; these
# are for Parser-local code.

UINT16_LE = struct.Struct("<H")
UINT32_LE = struct.Struct("<I")
UINT64_LE = struct.Struct("<Q")
INT32_LE = struct.Struct("<i")
FLOAT32_LE = struct.Struct("<f")

# The generic chunk header shared by every iTunesDB chunk:
#   +0x00  chunk_type  (4 bytes ASCII)
#   +0x04  header_len  (u32 LE)
#   +0x08  length_or_child_count  (u32 LE)
_GENERIC_HEADER = struct.Struct("<4sII")
GENERIC_HEADER_SIZE = _GENERIC_HEADER.size  # 12 bytes

ParseResult = dict[str, Any]
"""Return type of every chunk parser: ``{"next_offset": int, "data": ...}``."""


def read_generic_header(
    data: bytes | bytearray,
    offset: int,
) -> tuple[str, int, int]:
    """Read the 12-byte generic chunk header at *offset*.

    Returns:
        Tuple of ``(chunk_type, header_length, length_or_child_count)``.

    Raises:
        InsufficientDataError: If fewer than 12 bytes remain at *offset*.
        CorruptHeaderError: If the chunk type bytes are not valid ASCII.
    """
    end = offset + GENERIC_HEADER_SIZE
    if end > len(data):
        raise InsufficientDataError(offset, GENERIC_HEADER_SIZE, len(data) - offset)

    raw_type, header_length, length_or_children = _GENERIC_HEADER.unpack_from(data, offset)

    try:
        chunk_type = raw_type.decode("ascii")
    except UnicodeDecodeError:
        raise CorruptHeaderError(
            offset,
            f"chunk type bytes are not valid ASCII: {raw_type!r}",
        )

    return chunk_type, header_length, length_or_children
