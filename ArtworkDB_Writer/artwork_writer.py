"""
ArtworkDB Writer for iPod Classic.

Writes the ArtworkDB binary file and associated .ithmb image files.

ArtworkDB structure:
    mhfd (file header)
      mhsd type=1 → mhli → mhii[] (image entries, one per unique album art)
        Each mhii has MHOD type=2 children containing MHNI (one per image format)
        Each MHNI has an MHOD type=3 child with the ithmb filename
      mhsd type=2 → mhla (empty, not used for music artwork)
      mhsd type=3 → mhlf → mhif[] (one per image format, describes ithmb file sizes)
"""

import logging
import os
import struct
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import StrEnum

from ipod_device import ArtworkFormat

from .art_extractor import art_hash, extract_art_with_folder
from .ithmb_codecs import (
    decode_pixels_for_format,
    encode_image_for_format,
    expected_size_bytes,
)
from .rgb565 import IPOD_STRIDE_OVERRIDE, get_artwork_format_definitions, get_artwork_formats, image_from_bytes

logger = logging.getLogger(__name__)

# Header sizes (from real iPod Classic ArtworkDB)
MHFD_HEADER_SIZE = 132
MHSD_HEADER_SIZE = 96
MHLI_HEADER_SIZE = 92
MHLA_HEADER_SIZE = 92
MHLF_HEADER_SIZE = 92
MHII_HEADER_SIZE = 152
MHOD_HEADER_SIZE = 24
MHNI_HEADER_SIZE = 76
MHIF_HEADER_SIZE = 124


@dataclass
class ArtworkEntry:
    """Represents a unique album art image for the ArtworkDB."""
    img_id: int
    db_track_id: int  # db_track_id of one associated track
    art_hash: str | None  # MD5 hash for deduplication when sourced from PC
    src_img_size: int  # Size of original source image
    formats: dict = field(default_factory=dict)  # Per-format converted data: {format_id: {data, width, height, size, ...}}
    db_track_ids: list = field(default_factory=list)  # Track db_track_ids that use this artwork

    @property
    def track_db_id(self) -> int:
        """Backward-compatible alias for db_track_id."""
        return self.db_track_id

    @track_db_id.setter
    def track_db_id(self, value: int) -> None:
        self.db_track_id = value


@dataclass
class PendingArtworkWrite:
    """Result of a deferred write_artworkdb call.

    Holds the db_track_id to img_id mapping and temp file paths.  The caller must
    call ``commit()`` after the iTunesDB/CDB is also ready to ensure both
    databases are updated atomically.  Call ``abort()`` to clean up temp
    files without committing.
    """
    db_track_id_to_art_info: dict          # db_track_id → (img_id, src_img_size)
    _pending_renames: list = field(default_factory=list)  # [(temp, final), ...]
    _post_commit_cleanup: Callable[[], None] | None = None
    _committed: bool = False

    @property
    def db_id_to_art_info(self) -> dict:
        """Backward-compatible alias for db_track_id_to_art_info."""
        return self.db_track_id_to_art_info

    # Dict-like interface for compatibility with code expecting a plain dict
    def __getitem__(self, key):
        """Allow indexing like a dict: pending_aw[track_id] → (img_id, size)"""
        return self.db_track_id_to_art_info[key]

    def __setitem__(self, key, value):
        """Allow dict-like assignment."""
        self.db_track_id_to_art_info[key] = value

    def __contains__(self, key) -> bool:
        """Allow 'in' operator."""
        return key in self.db_track_id_to_art_info

    def __iter__(self):
        """Allow iteration over keys."""
        return iter(self.db_track_id_to_art_info)

    def __len__(self) -> int:
        """Allow len()."""
        return len(self.db_track_id_to_art_info)

    def get(self, key, default=None):
        """Dict-like get() with default."""
        return self.db_track_id_to_art_info.get(key, default)

    def keys(self):
        """Return dict keys."""
        return self.db_track_id_to_art_info.keys()

    def values(self):
        """Return dict values."""
        return self.db_track_id_to_art_info.values()

    def items(self):
        """Return dict items."""
        return self.db_track_id_to_art_info.items()

    def commit(self) -> None:
        """Atomically replace all temp files with final paths."""
        if self._committed:
            return
        for temp, final in self._pending_renames:
            os.replace(temp, final)
        if self._post_commit_cleanup is not None:
            self._post_commit_cleanup()
        self._committed = True

    def abort(self) -> None:
        """Remove all temp files without committing."""
        if self._committed:
            return
        for temp, _final in self._pending_renames:
            try:
                os.remove(temp)
            except OSError:
                pass


class ArtworkDecisionKind(StrEnum):
    """Per-track action for the final artwork state."""

    NEW_FROM_PC = "new_from_pc"
    PRESERVE_EXISTING = "preserve_existing"
    CLEAR_ART = "clear_art"
    PRESERVE_FALLBACK = "preserve_fallback"


@dataclass(frozen=True)
class ArtworkAssetRef:
    """Identifies the shared artwork payload for dedupe/reuse."""

    source: str
    value: str | int


@dataclass
class TrackArtworkDecision:
    """Resolved artwork action for one track in the final database."""

    db_track_id: int
    kind: ArtworkDecisionKind
    asset_ref: ArtworkAssetRef | None = None
    art_bytes: bytes | None = None
    src_img_size: int = 0
    existing_entry: dict | None = None


@dataclass
class ArtworkDecisionSummary:
    """Counters for structured writer logging."""

    preserved_unchanged: int = 0
    preserved_fallback: int = 0
    reencoded: int = 0
    cleared: int = 0
    shared_from_album: int = 0
    salvaged: int = 0
    dropped_invalid: int = 0


def _write_mhod_string(mhod_type: int, string: str) -> bytes:
    """Write an ArtworkDB MHOD string (type 1 or 3).

    Type 3 (ithmb filename) uses UTF-16LE encoding (encoding byte = 2),
    matching real iPod Classic databases.
    """
    # Type 3 (filename) uses UTF-16LE; others use UTF-8
    if mhod_type == 3:
        encoded = string.encode('utf-16-le')
        encoding_byte = 2
    else:
        encoded = string.encode('utf-8')
        encoding_byte = 1

    str_len = len(encoded)

    # Pad to 4-byte boundary
    padding = (4 - (str_len % 4)) % 4

    # String body: str_len(4) + encoding(1) + unk(3) + unk2(4) + string + padding
    body = struct.pack('<I', str_len)       # string byte length
    body += struct.pack('<B', encoding_byte)
    body += b'\x00' * 3                    # unknown
    body += b'\x00' * 4                    # unknown
    body += encoded
    body += b'\x00' * padding

    total_len = MHOD_HEADER_SIZE + len(body)

    # MHOD header
    header = bytearray(MHOD_HEADER_SIZE)
    header[0:4] = b'mhod'
    struct.pack_into('<I', header, 4, MHOD_HEADER_SIZE)
    struct.pack_into('<I', header, 8, total_len)
    struct.pack_into('<H', header, 12, mhod_type)

    return bytes(header) + body


def _write_mhni(format_id: int, ithmb_offset: int, img_info: dict) -> bytes:
    """
    Write an MHNI (image name/location) chunk.

    Args:
        format_id: Correlation ID (1055, 1060, 1061)
        ithmb_offset: Byte offset within the ithmb file
        img_info: Dict with width, height, size from rgb565 conversion
    """
    # Write the filename MHOD (type 3) first to know total size
    filename = f":F{format_id}_1.ithmb"
    mhod3 = _write_mhod_string(3, filename)

    total_len = MHNI_HEADER_SIZE + len(mhod3)

    visible_h = int(img_info['height'])
    visible_w = int(img_info['width'])
    img_size = int(img_info['size'])

    stride = int(img_info.get('stride_pixels', IPOD_STRIDE_OVERRIDE.get(format_id, visible_w)))
    if stride < visible_w:
        stride = visible_w

    vertical_padding = max(0, int(img_info.get('vpad', 0) or 0))
    horizontal_padding = max(0, int(img_info.get('hpad', 0) or 0))
    if vertical_padding == 0 and horizontal_padding == 0:
        expected_size = expected_size_bytes(format_id, visible_w, visible_h, stride_pixels=stride)
        if expected_size > 0 and expected_size != img_size:
            logger.debug(
                "ART: MHNI size mismatch for fmt %d: size=%d expected=%d; preserving stored dims",
                format_id,
                img_size,
                expected_size,
            )

    header = bytearray(MHNI_HEADER_SIZE)
    header[0:4] = b'mhni'
    struct.pack_into('<I', header, 4, MHNI_HEADER_SIZE)
    struct.pack_into('<I', header, 8, total_len)
    struct.pack_into('<I', header, 12, 1)               # child count (1 = the filename MHOD)
    struct.pack_into('<I', header, 16, format_id)        # correlationID
    struct.pack_into('<I', header, 20, ithmb_offset)     # offset in ithmb file
    struct.pack_into('<I', header, 24, img_size)          # image data size in bytes
    if vertical_padding > 0x7FFF or horizontal_padding > 0x7FFF:
        raise ValueError(
            f"MHNI padding too large for format {format_id}: vpad={vertical_padding} hpad={horizontal_padding}"
        )
    struct.pack_into('<h', header, 28, vertical_padding)
    struct.pack_into('<h', header, 30, horizontal_padding)
    struct.pack_into('<H', header, 32, visible_h)
    struct.pack_into('<H', header, 34, visible_w)
    # offset 36: unk1 = 0
    struct.pack_into('<I', header, 40, img_size)          # imgSize2 (same as imgSize)

    return bytes(header) + mhod3


def _write_mhod_container(mhod_type: int, mhni_data: bytes) -> bytes:
    """Write a container MHOD (type 2 or 5) wrapping an MHNI."""
    total_len = MHOD_HEADER_SIZE + len(mhni_data)

    header = bytearray(MHOD_HEADER_SIZE)
    header[0:4] = b'mhod'
    struct.pack_into('<I', header, 4, MHOD_HEADER_SIZE)
    struct.pack_into('<I', header, 8, total_len)
    struct.pack_into('<H', header, 12, mhod_type)

    return bytes(header) + mhni_data


def _write_mhii(entry: ArtworkEntry, format_offsets: dict) -> bytes:
    """
    Write an MHII (image item) chunk.

    Args:
        entry: ArtworkEntry with converted format data
        format_offsets: {format_id: current_offset} for ithmb file positions
    """
    # Build MHOD children (one per format)
    children = []
    for fmt_id in sorted(entry.formats.keys()):
        img_info = entry.formats[fmt_id]
        offset = format_offsets.get(fmt_id, 0)
        mhni = _write_mhni(fmt_id, offset, img_info)
        mhod = _write_mhod_container(2, mhni)
        children.append(mhod)

    # NOTE: libgpod does NOT write MHOD type 6 / mhaf children in MHII.
    # Earlier versions of this code added one but it confused Nano 2G firmware.

    children_data = b''.join(children)
    total_len = MHII_HEADER_SIZE + len(children_data)

    header = bytearray(MHII_HEADER_SIZE)
    header[0:4] = b'mhii'
    struct.pack_into('<I', header, 4, MHII_HEADER_SIZE)
    struct.pack_into('<I', header, 8, total_len)
    struct.pack_into('<I', header, 12, len(children))   # child count
    struct.pack_into('<I', header, 16, entry.img_id)
    struct.pack_into('<Q', header, 20, entry.db_track_id)    # db_track_id of first track
    # offset 28: unk1 = 0
    # offset 32: rating = 0
    # offset 36: unk2 = 0
    # offset 40: originalDate = 0
    # offset 44: exifTakenDate = 0
    struct.pack_into('<I', header, 48, entry.src_img_size)  # source image size
    # offset 56 and 60: libgpod defaults these to 0 for new artwork
    # (our old code had 9 and 1 copied from an iPod Classic db, which
    #  may confuse older/simpler firmware like Nano 2G)

    return bytes(header) + children_data


def _write_mhli(entries: list[ArtworkEntry], format_offsets_map: dict) -> bytes:
    """Write MHLI (image list) containing all MHII entries."""
    mhii_data = []
    for entry in entries:
        mhii = _write_mhii(entry, format_offsets_map[entry.img_id])
        mhii_data.append(mhii)

    children_data = b''.join(mhii_data)

    header = bytearray(MHLI_HEADER_SIZE)
    header[0:4] = b'mhli'
    struct.pack_into('<I', header, 4, MHLI_HEADER_SIZE)
    struct.pack_into('<I', header, 8, len(entries))  # count (NOT total_length for mhli)
    # Rest of header is zeros/padding

    return bytes(header) + children_data


def _write_mhla() -> bytes:
    """Write empty MHLA (album list, not used for music artwork)."""
    header = bytearray(MHLA_HEADER_SIZE)
    header[0:4] = b'mhla'
    struct.pack_into('<I', header, 4, MHLA_HEADER_SIZE)
    struct.pack_into('<I', header, 8, 0)  # count = 0
    return bytes(header)


def _write_mhif(format_id: int, image_size: int) -> bytes:
    """
    Write MHIF (file info) entry.

    Args:
        format_id: Correlation ID
        image_size: Size in bytes of ONE image in this format
    """
    header = bytearray(MHIF_HEADER_SIZE)
    header[0:4] = b'mhif'
    struct.pack_into('<I', header, 4, MHIF_HEADER_SIZE)
    struct.pack_into('<I', header, 8, MHIF_HEADER_SIZE)
    # offset 12: unk = 0
    struct.pack_into('<I', header, 16, format_id)    # correlationID
    struct.pack_into('<I', header, 20, image_size)   # image size per entry
    return bytes(header)


def _write_mhlf(format_ids: list[int], image_sizes: dict) -> bytes:
    """Write MHLF (file list) containing MHIF entries."""
    mhif_data = []
    for fmt_id in format_ids:
        mhif = _write_mhif(fmt_id, image_sizes[fmt_id])
        mhif_data.append(mhif)

    children_data = b''.join(mhif_data)

    header = bytearray(MHLF_HEADER_SIZE)
    header[0:4] = b'mhlf'
    struct.pack_into('<I', header, 4, MHLF_HEADER_SIZE)
    struct.pack_into('<I', header, 8, len(format_ids))  # count
    return bytes(header) + children_data


def _write_mhsd(ds_type: int, child_data: bytes) -> bytes:
    """Write MHSD (dataset) wrapping a child list."""
    total_len = MHSD_HEADER_SIZE + len(child_data)

    header = bytearray(MHSD_HEADER_SIZE)
    header[0:4] = b'mhsd'
    struct.pack_into('<I', header, 4, MHSD_HEADER_SIZE)
    struct.pack_into('<I', header, 8, total_len)
    struct.pack_into('<H', header, 12, ds_type)
    return bytes(header) + child_data


def _write_mhfd(datasets: list[bytes], next_mhii_id: int,
                reference_mhfd: bytes | None = None) -> bytes:
    """
    Write MHFD (file header) for ArtworkDB.

    Args:
        datasets: List of serialized MHSD chunks
        next_mhii_id: Next available image ID
        reference_mhfd: Reference ArtworkDB to copy unk fields from
    """
    all_data = b''.join(datasets)
    total_len = MHFD_HEADER_SIZE + len(all_data)

    header = bytearray(MHFD_HEADER_SIZE)
    header[0:4] = b'mhfd'
    struct.pack_into('<I', header, 4, MHFD_HEADER_SIZE)
    struct.pack_into('<I', header, 8, total_len)
    # offset 12: unk1 = 0
    struct.pack_into('<I', header, 16, 2)                # unk2 = 2 (per libgpod, always 2)
    struct.pack_into('<I', header, 20, len(datasets))    # childCount
    # offset 24: unk3 = 0
    struct.pack_into('<I', header, 28, next_mhii_id)     # next_mhii_id

    # Copy unk4/unk5 from reference if available (unknown purpose but present)
    if reference_mhfd and len(reference_mhfd) >= 48:
        header[32:48] = reference_mhfd[32:48]

    struct.pack_into('<I', header, 48, 2)  # unk6 = 2 (always 2)

    # Copy unk9/unk10 from reference if available
    if reference_mhfd and len(reference_mhfd) >= 68:
        header[60:68] = reference_mhfd[60:68]

    return bytes(header) + all_data


def _read_existing_artwork(artworkdb_path: str, artwork_dir: str) -> dict:
    """
    Read existing artwork entries from ArtworkDB and ithmb files.

    Parses the binary ArtworkDB directly (not via the parser, which is lossy
    for multi-format MHII entries), then reads raw pixel data from existing
    ithmb files.

    Returns:
        Dict mapping img_id → {
            'song_id': int,
            'src_img_size': int,
            'formats': {format_id: bytes},  # raw RGB565 pixel data
        }
    """
    if not os.path.exists(artworkdb_path):
        return {}

    try:
        with open(artworkdb_path, 'rb') as f:
            data = f.read()
    except Exception as e:
        logger.warning(f"ART: failed to read existing ArtworkDB: {e}")
        return {}

    if len(data) < 32 or data[:4] != b'mhfd':
        return {}

    entries = {}

    # Walk mhfd → mhsd datasets → find type 1 (image list) → mhli → mhii[]
    mhfd_header_size = struct.unpack('<I', data[4:8])[0]
    child_count = struct.unpack('<I', data[20:24])[0]

    offset = mhfd_header_size
    for _ in range(child_count):
        if offset + 14 > len(data) or data[offset:offset + 4] != b'mhsd':
            break
        mhsd_header = struct.unpack('<I', data[offset + 4:offset + 8])[0]
        mhsd_total = struct.unpack('<I', data[offset + 8:offset + 12])[0]
        ds_type = struct.unpack('<H', data[offset + 12:offset + 14])[0]

        if ds_type == 1:
            # Image list dataset — walk mhli → mhii entries
            mhli_offset = offset + mhsd_header
            if mhli_offset + 12 <= len(data) and data[mhli_offset:mhli_offset + 4] == b'mhli':
                mhli_header = struct.unpack('<I', data[mhli_offset + 4:mhli_offset + 8])[0]
                mhii_count = struct.unpack('<I', data[mhli_offset + 8:mhli_offset + 12])[0]

                mhii_offset = mhli_offset + mhli_header
                for _ in range(mhii_count):
                    if mhii_offset + 52 > len(data) or data[mhii_offset:mhii_offset + 4] != b'mhii':
                        break
                    mhii_total = struct.unpack('<I', data[mhii_offset + 8:mhii_offset + 12])[0]
                    entry = _parse_mhii_existing(data, mhii_offset, artwork_dir)
                    if entry:
                        entries[entry['img_id']] = entry
                    mhii_offset += mhii_total

        offset += mhsd_total

    return entries


def _parse_mhii_existing(data: bytes, offset: int, artwork_dir: str) -> dict | None:
    """Parse a single MHII entry from the existing ArtworkDB.

    Returns location *references* (path / offset / size) for each format's
    pixel data rather than the pixel bytes themselves.  The caller resolves
    only the entries it actually needs, avoiding USB reads for artwork that
    won't be preserved.

    Return shape::

        {
          'img_id': int,
          'song_id': int,
          'src_img_size': int,
          'formats': {
              format_id: {'path': str, 'ithmb_offset': int, 'size': int}
          }
        }
    """
    header_size = struct.unpack('<I', data[offset + 4:offset + 8])[0]
    child_count = struct.unpack('<I', data[offset + 12:offset + 16])[0]
    img_id = struct.unpack('<I', data[offset + 16:offset + 20])[0]
    song_id = struct.unpack('<Q', data[offset + 20:offset + 28])[0]
    src_img_size = struct.unpack('<I', data[offset + 48:offset + 52])[0]

    # Walk children to find MHOD type 2 containers wrapping MHNI entries.
    # Record location references only — pixel data is NOT read here.
    formats: dict = {}
    child_offset = offset + header_size
    for _ in range(child_count):
        if child_offset + 14 > len(data) or data[child_offset:child_offset + 4] != b'mhod':
            break
        mhod_header = struct.unpack('<I', data[child_offset + 4:child_offset + 8])[0]
        mhod_total = struct.unpack('<I', data[child_offset + 8:child_offset + 12])[0]
        mhod_type = struct.unpack('<H', data[child_offset + 12:child_offset + 14])[0]

        if mhod_type == 2:
            mhni_offset = child_offset + mhod_header
            if (mhni_offset + 28 <= len(data) and data[mhni_offset:mhni_offset + 4] == b'mhni'):
                format_id = struct.unpack('<I', data[mhni_offset + 16:mhni_offset + 20])[0]
                ithmb_offset = struct.unpack('<I', data[mhni_offset + 20:mhni_offset + 24])[0]
                img_size = struct.unpack('<I', data[mhni_offset + 24:mhni_offset + 28])[0]

                ithmb_path = os.path.join(artwork_dir, f"F{format_id}_1.ithmb")
                if os.path.exists(ithmb_path) and img_size > 0:
                    vpad = struct.unpack('<h', data[mhni_offset + 28:mhni_offset + 30])[0]
                    hpad = struct.unpack('<h', data[mhni_offset + 30:mhni_offset + 32])[0]
                    img_h = struct.unpack('<H', data[mhni_offset + 32:mhni_offset + 34])[0]
                    img_w = struct.unpack('<H', data[mhni_offset + 34:mhni_offset + 36])[0]
                    formats[format_id] = {
                        'path': ithmb_path,
                        'ithmb_offset': ithmb_offset,
                        'size': img_size,
                        'width': img_w,
                        'height': img_h,
                        'hpad': hpad,
                        'vpad': vpad,
                    }

        child_offset += mhod_total

    if not formats:
        return None

    return {
        'img_id': img_id,
        'song_id': song_id,
        'src_img_size': src_img_size,
        'formats': formats,
    }


def _cleanup_stale_ithmb_files(artwork_dir: str, keep_format_ids: set[int], artdb_path: str | None = None) -> None:
    """Remove ithmb files that are not referenced by the final ArtworkDB.

    Historically this function removed files simply because their format
    ID wasn't present in `keep_format_ids`, which could falsely remove
    ithmb files that were still referenced by the newly-written ArtworkDB
    (for example when preserved entries or mixed-format databases exist).

    To be conservative, prefer parsing the on-disk `ArtworkDB` (if
    available) to determine the authoritative set of referenced format
    IDs. Any ithmb whose format ID is not referenced by the ArtworkDB
    and not in `keep_format_ids` is eligible for removal.
    """
    import re
    pattern = re.compile(r'^F(\d+)_\d+\.ithmb$', re.IGNORECASE)
    if not os.path.isdir(artwork_dir):
        return

    # Determine authoritative referenced formats from the new ArtworkDB
    referenced_formats: set[int] = set()
    try:
        if artdb_path is None:
            artdb_path = os.path.join(artwork_dir, "ArtworkDB")
        if artdb_path and os.path.exists(artdb_path):
            with open(artdb_path, 'rb') as f:
                data = f.read()
            # Reuse helper from rgb565 module if available, fallback to
            # simple parsing: extract mhif correlation IDs via _extract_format_ids
            try:
                from .rgb565 import _extract_format_ids
                referenced_formats.update(_extract_format_ids(data))
            except Exception:
                # Best-effort parsing: look for 'mhif' chunks and read corr_id
                import struct
                if len(data) >= 8 and data[:4] == b'mhfd':
                    mhfd_header = struct.unpack('<I', data[4:8])[0]
                    offset = mhfd_header
                    # scan for mhif signatures
                    while offset + 12 < len(data):
                        if data[offset:offset + 4] == b'mhif' and offset + 20 <= len(data):
                            corr_id = struct.unpack('<I', data[offset + 16:offset + 20])[0]
                            referenced_formats.add(corr_id)
                            size = struct.unpack('<I', data[offset + 4:offset + 8])[0]
                            offset += size
                        else:
                            offset += 4
    except Exception:
        # On any parsing failure, fall back to the provided keep_format_ids
        referenced_formats = set()

    # Final keep set: union of explicitly kept formats and those referenced
    keep = set(keep_format_ids) | set(referenced_formats)

    for name in os.listdir(artwork_dir):
        m = pattern.match(name)
        if m:
            fmt_id = int(m.group(1))
            if fmt_id not in keep:
                path = os.path.join(artwork_dir, name)
                try:
                    os.remove(path)
                    logger.info("ART: removed unreferenced ithmb file %s (format %d)", name, fmt_id)
                except OSError as e:
                    logger.warning("ART: failed to remove stale ithmb %s: %s", name, e)


def _decode_preserved_frame(ref: dict, format_id: int, pixel_bytes: bytes, fmt_override=None):
    """Decode one preserved frame using format-aware codec rules."""
    width = max(1, int(ref.get('width', 0) or 0))
    height = max(1, int(ref.get('height', 0) or 0))
    hpad = max(0, int(ref.get('hpad', 0) or 0))
    vpad = max(0, int(ref.get('vpad', 0) or 0))
    return decode_pixels_for_format(format_id, pixel_bytes, width, height, hpad, vpad)


def _get_track_artwork_hint(track) -> str:
    """Read the optional sync hint injected by the executor."""
    hint = _get_track_field(track, "_iop_artwork_sync_hint")
    return str(hint or "").strip().lower()


def _resolve_existing_art_entry(
    track,
    existing_art: dict[int, dict],
    existing_by_song_id: dict[int, int],
) -> tuple[int, dict] | None:
    """Resolve the currently linked artwork entry for a track, if any."""
    db_track_id = _get_track_field(track, "db_track_id")
    if not db_track_id:
        return None

    resolved_img_id = existing_by_song_id.get(db_track_id)
    if resolved_img_id is None:
        mhii_link = _get_track_field(track, "mhii_link")
        if not mhii_link:
            mhii_link = _get_track_field(track, "mhiiLink")
        if not mhii_link:
            mhii_link = _get_track_field(track, "artwork_id_ref")
        if mhii_link and mhii_link in existing_art:
            resolved_img_id = mhii_link

    if resolved_img_id is None:
        return None

    entry = existing_art.get(resolved_img_id)
    if not entry:
        return None
    return resolved_img_id, entry


def _validate_existing_format_ref(
    fmt_id: int,
    ref: dict,
    device_format_defs: Mapping[int, ArtworkFormat],
) -> dict | None:
    """Return normalized preserve metadata if an existing ref is safe to reuse."""
    w = max(1, int(ref.get("width", 0) or 0))
    h_dim = max(1, int(ref.get("height", 0) or 0))
    hpad = max(0, int(ref.get("hpad", 0) or 0))
    vpad = max(0, int(ref.get("vpad", 0) or 0))
    stored_w = max(1, w + hpad)
    stored_h = max(1, h_dim + vpad)
    expected_size = expected_size_bytes(
        fmt_id,
        stored_w,
        stored_h,
        stride_pixels=stored_w,
        fmt_override=device_format_defs.get(fmt_id),
    )
    if expected_size > 0 and int(ref.get("size", 0) or 0) != expected_size:
        return None

    return {
        "width": w,
        "height": h_dim,
        "size": int(ref.get("size", 0) or 0),
        "hpad": hpad,
        "vpad": vpad,
        "stride_pixels": stored_w,
        "path": ref.get("path"),
        "ithmb_offset": int(ref.get("ithmb_offset", 0) or 0),
    }


def _existing_entry_supports_all_formats(
    existing_entry: dict | None,
    required_format_ids: list[int],
    device_format_defs: Mapping[int, ArtworkFormat],
) -> dict[int, dict] | None:
    """Return per-format metadata when all required formats can be preserved."""
    if existing_entry is None:
        return None

    refs = existing_entry.get("formats", {})
    fmt_meta: dict[int, dict] = {}
    for fmt_id in required_format_ids:
        ref = refs.get(fmt_id)
        if ref is None:
            return None
        meta = _validate_existing_format_ref(fmt_id, ref, device_format_defs)
        if meta is None:
            return None
        fmt_meta[fmt_id] = meta
    return fmt_meta


def _build_existing_song_index(existing_art: dict[int, dict]) -> dict[int, int]:
    """Map ArtworkDB song_id -> img_id for authoritative artwork lookup."""
    existing_by_song_id: dict[int, int] = {}
    for img_id, entry in existing_art.items():
        sid = int(entry.get("song_id", 0) or 0)
        if sid:
            existing_by_song_id[sid] = img_id
    return existing_by_song_id


def _collect_track_artwork_decisions(
    tracks: list,
    pc_file_paths: dict[int, str],
    existing_art: dict[int, dict],
    required_format_ids: list[int],
    device_format_defs: Mapping[int, ArtworkFormat],
) -> tuple[dict[int, TrackArtworkDecision], ArtworkDecisionSummary]:
    """Resolve the desired final artwork state for every track."""
    decisions: dict[int, TrackArtworkDecision] = {}
    summary = ArtworkDecisionSummary()
    existing_by_song_id = _build_existing_song_index(existing_art)

    for track in tracks:
        db_track_id = _get_track_field(track, "db_track_id")
        if not db_track_id:
            title = _get_track_field(track, "title") or "?"
            logger.warning("ART: track '%s' has no db_track_id, skipping", title)
            continue

        resolved_existing = _resolve_existing_art_entry(track, existing_art, existing_by_song_id)
        existing_entry = resolved_existing[1] if resolved_existing else None
        existing_img_id = resolved_existing[0] if resolved_existing else 0
        preserve_ok = _existing_entry_supports_all_formats(
            existing_entry,
            required_format_ids,
            device_format_defs,
        ) is not None

        hint = _get_track_artwork_hint(track)
        pc_path = pc_file_paths.get(db_track_id)
        if hint == "clear_art":
            decisions[db_track_id] = TrackArtworkDecision(
                db_track_id=db_track_id,
                kind=ArtworkDecisionKind.CLEAR_ART,
                existing_entry=existing_entry,
            )
            summary.cleared += 1
            continue

        if (
            hint == "preserve_existing"
            and pc_path
            and os.path.exists(pc_path)
            and preserve_ok
            and existing_entry is not None
        ):
            decisions[db_track_id] = TrackArtworkDecision(
                db_track_id=db_track_id,
                kind=ArtworkDecisionKind.PRESERVE_EXISTING,
                asset_ref=ArtworkAssetRef("preserve", existing_img_id),
                src_img_size=int(existing_entry.get("src_img_size", 0) or 0),
                existing_entry=existing_entry,
            )
            summary.preserved_unchanged += 1
            continue

        if not pc_path:
            if preserve_ok and existing_entry is not None:
                decisions[db_track_id] = TrackArtworkDecision(
                    db_track_id=db_track_id,
                    kind=ArtworkDecisionKind.PRESERVE_FALLBACK,
                    asset_ref=ArtworkAssetRef("preserve", existing_img_id),
                    src_img_size=int(existing_entry.get("src_img_size", 0) or 0),
                    existing_entry=existing_entry,
                )
                summary.preserved_fallback += 1
            else:
                decisions[db_track_id] = TrackArtworkDecision(
                    db_track_id=db_track_id,
                    kind=ArtworkDecisionKind.CLEAR_ART,
                    existing_entry=existing_entry,
                )
                summary.cleared += 1
            continue

        if not os.path.exists(pc_path):
            title = _get_track_field(track, "title") or "?"
            logger.warning("ART: PC file not found for '%s': %s", title, pc_path)
            if preserve_ok and existing_entry is not None:
                decisions[db_track_id] = TrackArtworkDecision(
                    db_track_id=db_track_id,
                    kind=ArtworkDecisionKind.PRESERVE_FALLBACK,
                    asset_ref=ArtworkAssetRef("preserve", existing_img_id),
                    src_img_size=int(existing_entry.get("src_img_size", 0) or 0),
                    existing_entry=existing_entry,
                )
                summary.preserved_fallback += 1
            else:
                decisions[db_track_id] = TrackArtworkDecision(
                    db_track_id=db_track_id,
                    kind=ArtworkDecisionKind.CLEAR_ART,
                    existing_entry=existing_entry,
                )
                summary.cleared += 1
            continue

        art_bytes = extract_art_with_folder(pc_path)
        if art_bytes is None:
            decisions[db_track_id] = TrackArtworkDecision(
                db_track_id=db_track_id,
                kind=ArtworkDecisionKind.CLEAR_ART,
                existing_entry=existing_entry,
            )
            summary.cleared += 1
            title = _get_track_field(track, "title") or "?"
            logger.debug("ART: no art found for '%s' (%s)", title, pc_path)
            continue

        digest = art_hash(art_bytes)
        decisions[db_track_id] = TrackArtworkDecision(
            db_track_id=db_track_id,
            kind=ArtworkDecisionKind.NEW_FROM_PC,
            asset_ref=ArtworkAssetRef("pc", digest),
            art_bytes=art_bytes,
            src_img_size=len(art_bytes),
            existing_entry=existing_entry,
        )
        summary.reencoded += 1

    return decisions, summary


def _convert_new_pc_art(
    decisions: dict[int, TrackArtworkDecision],
    device_formats: dict[int, tuple[int, int]],
    device_format_defs: Mapping[int, ArtworkFormat],
    progress_callback: Callable[[str], None] | None = None,
) -> dict[ArtworkAssetRef, dict]:
    """Convert only the PC-sourced artwork payloads that need re-encoding."""
    pc_art_map: dict[ArtworkAssetRef, bytes] = {}
    for decision in decisions.values():
        if decision.kind != ArtworkDecisionKind.NEW_FROM_PC:
            continue
        if decision.asset_ref is None or decision.art_bytes is None:
            continue
        pc_art_map[decision.asset_ref] = decision.art_bytes

    unique_converted: dict[ArtworkAssetRef, dict] = {}
    if not pc_art_map:
        return unique_converted

    if progress_callback is not None:
        progress_callback(
            f"Artwork — converting {len(pc_art_map)} image{'s' if len(pc_art_map) != 1 else ''}"
        )

    def _convert_one(asset_ref: ArtworkAssetRef, art_bytes: bytes) -> tuple[ArtworkAssetRef, dict | None]:
        img = image_from_bytes(art_bytes)
        if img is None:
            return asset_ref, None
        formats: dict = {}
        for fmt_id in sorted(device_formats.keys()):
            try:
                encoded = encode_image_for_format(
                    img,
                    fmt_id,
                    *device_formats[fmt_id],
                    fmt_override=device_format_defs.get(fmt_id),
                )
                formats[fmt_id] = encoded
            except Exception as exc:
                logger.debug(
                    "ART: format %d conversion failed for %s: %s",
                    fmt_id,
                    asset_ref,
                    exc,
                )
        return asset_ref, {"formats": formats, "src_img_size": len(art_bytes)} if formats else None

    n_workers = max(1, min(len(pc_art_map), os.cpu_count() or 4))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = {
            pool.submit(_convert_one, asset_ref, art_bytes): asset_ref
            for asset_ref, art_bytes in pc_art_map.items()
        }
        for fut in as_completed(futs):
            asset_ref, result = fut.result()
            if result is not None:
                unique_converted[asset_ref] = result
    return unique_converted


def _load_preserved_art_payloads(
    decisions: dict[int, TrackArtworkDecision],
    required_format_ids: list[int],
    device_formats: dict[int, tuple[int, int]],
    device_format_defs: Mapping[int, ArtworkFormat],
) -> tuple[dict[ArtworkAssetRef, dict], int, int]:
    """Load or salvage preserved on-device artwork for reuse."""
    preserve_entries: dict[ArtworkAssetRef, dict] = {}
    preserve_refs: dict[ArtworkAssetRef, dict] = {}
    ref_by_file_fmt: dict[tuple[str, int], list[tuple[int, ArtworkAssetRef, int]]] = defaultdict(list)

    for decision in decisions.values():
        if decision.kind not in (
            ArtworkDecisionKind.PRESERVE_EXISTING,
            ArtworkDecisionKind.PRESERVE_FALLBACK,
        ):
            continue
        if decision.asset_ref is None or decision.existing_entry is None:
            continue
        if decision.asset_ref in preserve_entries or decision.asset_ref in preserve_refs:
            continue

        fmt_meta = _existing_entry_supports_all_formats(
            decision.existing_entry,
            required_format_ids,
            device_format_defs,
        )
        if fmt_meta is not None:
            preserve_entries[decision.asset_ref] = {
                "fmt_meta": fmt_meta,
                "src_img_size": int(decision.existing_entry.get("src_img_size", 0) or 0),
            }
            for fmt_id, meta in fmt_meta.items():
                ref_by_file_fmt[(meta["path"], fmt_id)].append(
                    (meta["ithmb_offset"], decision.asset_ref, meta["size"])
                )
        else:
            preserve_refs[decision.asset_ref] = {
                "refs": decision.existing_entry.get("formats", {}),
                "src_img_size": int(decision.existing_entry.get("src_img_size", 0) or 0),
            }

    pixel_cache: dict[tuple[ArtworkAssetRef, int], bytes] = {}
    for (ithmb_path, fmt_id), items in ref_by_file_fmt.items():
        items.sort()
        try:
            with open(ithmb_path, "rb") as src:
                for ithmb_offset, asset_ref, size in items:
                    src.seek(ithmb_offset)
                    pixel_bytes = src.read(size)
                    if len(pixel_bytes) == size:
                        pixel_cache[(asset_ref, fmt_id)] = pixel_bytes
                    else:
                        logger.debug("ART: short read for preserved %s fmt %d", asset_ref, fmt_id)
        except OSError as exc:
            logger.warning("ART: failed to read preserved ithmb %s: %s", ithmb_path, exc)

    unique_converted: dict[ArtworkAssetRef, dict] = {}
    dropped_invalid = 0
    for asset_ref, meta in preserve_entries.items():
        formats = {}
        for fmt_id, dims in meta["fmt_meta"].items():
            pixel_bytes = pixel_cache.get((asset_ref, fmt_id))
            if pixel_bytes:
                formats[fmt_id] = {
                    "data": pixel_bytes,
                    "width": dims["width"],
                    "height": dims["height"],
                    "size": dims["size"],
                    "hpad": dims.get("hpad", 0),
                    "vpad": dims.get("vpad", 0),
                    "stride_pixels": dims.get("stride_pixels", dims["width"]),
                }
        if len(formats) == len(required_format_ids):
            unique_converted[asset_ref] = {
                "formats": formats,
                "src_img_size": meta["src_img_size"],
            }
        else:
            dropped_invalid += 1

    salvaged = 0
    for asset_ref, meta in preserve_refs.items():
        if asset_ref in unique_converted:
            continue

        source_img = None
        for fmt_id, ref in meta["refs"].items():
            try:
                with open(ref["path"], "rb") as src:
                    src.seek(ref["ithmb_offset"])
                    pixel_bytes = src.read(ref["size"])
                if len(pixel_bytes) != ref["size"]:
                    continue
                source_img = _decode_preserved_frame(
                    ref,
                    int(fmt_id),
                    pixel_bytes,
                    fmt_override=device_format_defs.get(int(fmt_id)),
                )
                if source_img is not None:
                    break
            except OSError:
                continue

        if source_img is None:
            dropped_invalid += 1
            continue

        formats = {}
        for fmt_id in required_format_ids:
            try:
                formats[fmt_id] = encode_image_for_format(
                    source_img,
                    fmt_id,
                    *device_formats[fmt_id],
                    fmt_override=device_format_defs.get(fmt_id),
                )
            except Exception as exc:
                logger.debug("ART: salvage re-encode failed for %s fmt %d: %s", asset_ref, fmt_id, exc)
        if len(formats) == len(required_format_ids):
            unique_converted[asset_ref] = {
                "formats": formats,
                "src_img_size": meta["src_img_size"],
            }
            salvaged += 1
        else:
            dropped_invalid += 1

    return unique_converted, salvaged, dropped_invalid


def write_artworkdb(
    ipod_path: str,
    tracks: list,
    pc_file_paths: dict | None = None,
    start_img_id: int = 100,
    reference_artdb_path: str | None = None,
    artwork_formats: dict[int, tuple[int, int]] | None = None,
    defer_commit: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> dict | PendingArtworkWrite:
    """
    Write ArtworkDB and ithmb files for an iPod.

    This function:
    1. Extracts album art from PC source files
    2. Preserves existing art for tracks without PC source files
    3. Converts art to RGB565 at multiple sizes
    4. Writes ithmb files (pixel data) to temp paths
    5. Writes ArtworkDB binary (metadata) to temp path
    6. Returns a mapping of track db_track_id to img_id for iTunesDB mhiiLink

    When ``defer_commit=True``, files are written to temp paths but NOT
    renamed to their final locations.  The caller receives a
    ``PendingArtworkWrite`` object and must call ``.commit()`` after the
    iTunesDB is also ready, or ``.abort()`` on failure.  This ensures
    both databases are updated atomically.

    Args:
        ipod_path: iPod mount point (e.g., "E:" or "/media/ipod")
        tracks: List of track dicts or TrackInfo objects with at least 'db_track_id' and 'album'
        pc_file_paths: Dict mapping track db_track_id → PC source file path
                       (if None, tries to extract art from iPod copies)
        start_img_id: Starting image ID (default 100, matching iTunes behavior)
        reference_artdb_path: Path to existing ArtworkDB for copying header fields
        artwork_formats: Device-specific format table {correlationID: (w,h)}.
                         If None, auto-detected from existing ArtworkDB / SysInfo.
        defer_commit: If True, return a PendingArtworkWrite instead of committing
                      immediately.

    Returns:
        If ``defer_commit=False`` (default): dict mapping track db_track_id →
        (img_id, src_img_size), or empty dict if no artwork found.

        If ``defer_commit=True``: a ``PendingArtworkWrite`` with the
        mapping in ``.db_track_id_to_art_info`` and a ``.commit()`` method.
    """
    artwork_dir = os.path.join(ipod_path, "iPod_Control", "Artwork")
    os.makedirs(artwork_dir, exist_ok=True)

    def _prog(msg: str) -> None:
        if progress_callback is not None:
            progress_callback(msg)

    normalized_pc_paths: dict[int, str] = {}
    if pc_file_paths:
        for key, path in pc_file_paths.items():
            try:
                db_track_id = int(key)
            except (TypeError, ValueError):
                continue
            if db_track_id > 0:
                normalized_pc_paths[db_track_id] = str(path)

    if artwork_formats is None:
        artwork_formats = get_artwork_formats(ipod_path)
    device_formats = artwork_formats
    device_format_defs = get_artwork_format_definitions(ipod_path)
    required_format_ids = sorted(device_formats.keys())
    logger.info("ART: using formats %s", required_format_ids)

    ref_mhfd = None
    if reference_artdb_path and os.path.exists(reference_artdb_path):
        with open(reference_artdb_path, "rb") as f:
            ref_mhfd = f.read()

    artworkdb_path = os.path.join(artwork_dir, "ArtworkDB")
    existing_art = _read_existing_artwork(artworkdb_path, artwork_dir)
    if existing_art:
        logger.info("ART: read %d existing image entries from ArtworkDB", len(existing_art))

    _prog(f"Artwork — scanning {len(tracks)} tracks")
    decisions, decision_summary = _collect_track_artwork_decisions(
        tracks,
        normalized_pc_paths,
        existing_art,
        required_format_ids,
        device_format_defs,
    )
    logger.info(
        "ART decisions: preserve=%d fallback=%d reencode=%d clear=%d",
        decision_summary.preserved_unchanged,
        decision_summary.preserved_fallback,
        decision_summary.reencoded,
        decision_summary.cleared,
    )

    unique_converted = _convert_new_pc_art(
        decisions,
        device_formats,
        device_format_defs,
        progress_callback=progress_callback,
    )
    preserved_converted, salvaged_preserved, dropped_invalid = _load_preserved_art_payloads(
        decisions,
        required_format_ids,
        device_formats,
        device_format_defs,
    )
    unique_converted.update(preserved_converted)
    decision_summary.salvaged = salvaged_preserved
    decision_summary.dropped_invalid += dropped_invalid

    if salvaged_preserved:
        logger.info(
            "ART: salvaged %d preserved artwork entr%s via decode/re-encode fallback",
            salvaged_preserved,
            "ies" if salvaged_preserved != 1 else "y",
        )

    entries: list[ArtworkEntry] = []
    entry_asset_keys: dict[int, ArtworkAssetRef] = {}
    img_id = start_img_id

    for track in tracks:
        db_track_id = _get_track_field(track, "db_track_id")
        if not db_track_id:
            continue
        decision = decisions.get(db_track_id)
        if decision is None or decision.kind == ArtworkDecisionKind.CLEAR_ART:
            continue
        if decision.asset_ref is None:
            continue
        converted = unique_converted.get(decision.asset_ref)
        if converted is None:
            decision_summary.dropped_invalid += 1
            continue

        entry = ArtworkEntry(
            img_id=img_id,
            db_track_id=db_track_id,
            art_hash=str(decision.asset_ref.value) if decision.asset_ref.source == "pc" else None,
            src_img_size=int(converted["src_img_size"]),
            db_track_ids=[db_track_id],
        )
        entry.formats = converted["formats"]
        entries.append(entry)
        entry_asset_keys[entry.img_id] = decision.asset_ref
        img_id += 1

    logger.info(
        "ART result: %d live entries from %d unique payloads (%d dropped invalid)",
        len(entries),
        len(set(entry_asset_keys.values())),
        decision_summary.dropped_invalid,
    )

    n_unique = len(set(entry_asset_keys.values()))
    if n_unique:
        _prog(f"Artwork — writing {n_unique} image{'s' if n_unique != 1 else ''} to device")
    else:
        _prog("Artwork — clearing device artwork")

    # --- Step 3: Write ithmb files ---
    format_ids = sorted({fmt_id for entry in entries for fmt_id in entry.formats.keys()})
    # Track current offset per format (for ithmb file append position)
    ithmb_offsets = {fmt_id: 0 for fmt_id in format_ids}
    # Map entry img_id → {format_id: offset} for MHNI
    format_offsets_map = {}
    # Track image sizes for MHIF (one size per format across all entries).
    # Use observed payload sizes so preserved mixed-format databases don't get
    # forced into current-device assumptions.
    image_sizes = {}
    for fmt_id in format_ids:
        observed_sizes = [
            int(entry.formats[fmt_id]['size'])
            for entry in entries
            if fmt_id in entry.formats and int(entry.formats[fmt_id]['size']) > 0
        ]
        if not observed_sizes:
            continue
        c = Counter(observed_sizes)
        image_sizes[fmt_id] = c.most_common(1)[0][0]
        if len(c) > 1:
            logger.warning(
                "ART: format %d has mixed payload sizes %s; using most common %d in MHIF",
                fmt_id,
                sorted(c.keys()),
                image_sizes[fmt_id],
            )

    # Write ithmb files to temp paths first — originals stay intact until
    # both ithmb AND ArtworkDB are fully written and verified.
    ithmb_temp_paths: dict[int, str] = {}  # fmt_id → temp path
    ithmb_final_paths: dict[int, str] = {}  # fmt_id → final path
    ithmb_files = {}
    # Track which unique images have been written to avoid ithmb duplication
    art_payload_written: dict[ArtworkAssetRef, dict[int, int]] = {}
    try:
        for fmt_id in format_ids:
            final = os.path.join(artwork_dir, f"F{fmt_id}_1.ithmb")
            temp = final + ".tmp"
            ithmb_final_paths[fmt_id] = final
            ithmb_temp_paths[fmt_id] = temp
            ithmb_files[fmt_id] = open(temp, 'wb')

        # Write each unique image only once; per-track entries sharing
        # the same art_hash reuse the same ithmb offsets.

        for entry in entries:
            asset_ref = entry_asset_keys[entry.img_id]
            if asset_ref in art_payload_written:
                # Already written — reuse offsets
                format_offsets_map[entry.img_id] = dict(art_payload_written[asset_ref])
            else:
                offsets = {}
                for fmt_id in format_ids:
                    if fmt_id in entry.formats:
                        img_data = entry.formats[fmt_id]['data']
                        offsets[fmt_id] = ithmb_offsets[fmt_id]
                        ithmb_files[fmt_id].write(img_data)
                        ithmb_offsets[fmt_id] += len(img_data)
                art_payload_written[asset_ref] = offsets
                format_offsets_map[entry.img_id] = dict(offsets)

        # Flush ithmb temp files to OS buffers.  No fsync here — these are
        # .tmp files and the os.replace renames below are the durability
        # boundary.  Fsyncing each ithmb file over USB adds multiple seconds
        # of blocked I/O with no safety benefit (the old files remain intact
        # until the rename succeeds).
    finally:
        for f in ithmb_files.values():
            f.close()

    # --- Step 4: Build ArtworkDB binary ---

    # Dataset 1: Image list
    mhli = _write_mhli(entries, format_offsets_map)
    ds1 = _write_mhsd(1, mhli)

    # Dataset 2: Album list (empty)
    mhla = _write_mhla()
    ds2 = _write_mhsd(2, mhla)

    # Dataset 3: File list
    mhlf = _write_mhlf(format_ids, image_sizes)
    ds3 = _write_mhsd(3, mhlf)

    # MHFD root
    next_id = start_img_id + len(entries)
    artdb_data = _write_mhfd([ds1, ds2, ds3], next_id, ref_mhfd)

    # Write ArtworkDB to temp file
    artdb_path = os.path.join(artwork_dir, "ArtworkDB")
    artdb_temp = artdb_path + ".tmp"
    try:
        with open(artdb_temp, 'wb') as f:
            f.write(artdb_data)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        # Clean up all temp files on failure
        for tp in ithmb_temp_paths.values():
            try:
                os.remove(tp)
            except OSError:
                pass
        try:
            os.remove(artdb_temp)
        except OSError:
            pass
        raise

    # --- Atomic commit: all temp files are complete, swap them in ---
    # os.replace is atomic on NTFS and POSIX — old files are only removed
    # when the new file is fully in place.

    # --- Step 5: Build db_track_id → (img_id, src_img_size) mapping ---
    # Each entry already has a unique img_id and a single song_id (db_track_id).
    db_track_id_to_art_info: dict[int, tuple[int, int]] = {}
    for entry in entries:
        db_track_id_to_art_info[entry.db_track_id] = (entry.img_id, entry.src_img_size)

    # Collect all pending renames (ithmb temps + artworkdb temp)
    pending_renames = []
    for fmt_id in format_ids:
        pending_renames.append((ithmb_temp_paths[fmt_id], ithmb_final_paths[fmt_id]))
    pending_renames.append((artdb_temp, artdb_path))

    def _post_commit_cleanup() -> None:
        # Prune only unreferenced ithmb files after the new ArtworkDB and
        # target ithmb files are fully committed.
        keep_format_ids = set(format_ids)
        _cleanup_stale_ithmb_files(artwork_dir, keep_format_ids)

    if defer_commit:
        logger.info(
            "ART: prepared %d unique images, %d MHII entries (per-track) — commit deferred",
            len(art_payload_written),
            len(entries),
        )
        return PendingArtworkWrite(
            db_track_id_to_art_info=db_track_id_to_art_info,
            _pending_renames=pending_renames,
            _post_commit_cleanup=_post_commit_cleanup,
        )

    # Immediate commit (legacy behaviour)
    try:
        for temp, final in pending_renames:
            os.replace(temp, final)
        _post_commit_cleanup()
    except Exception:
        # If any replace fails, clean up remaining temps
        for temp, _final in pending_renames:
            try:
                os.remove(temp)
            except OSError:
                pass
        raise

    logger.info(
        "Wrote ithmb files: %d unique images, %d MHII entries (per-track)",
        len(art_payload_written),
        len(entries),
    )
    for fmt_id in format_ids:
        size = os.path.getsize(ithmb_final_paths[fmt_id])
        logger.info(f"  F{fmt_id}_1.ithmb: {size} bytes")

    return db_track_id_to_art_info


def _get_track_field(track, field: str):
    """Get a field from a track dict or dataclass."""
    if isinstance(track, dict):
        if field == "db_track_id":
            return track.get("db_track_id", track.get("db_id"))
        return track.get(field)
    if field == "db_track_id":
        return getattr(track, "db_track_id", getattr(track, "db_id", None))
    return getattr(track, field, None)
