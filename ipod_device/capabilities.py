"""Device capabilities — per-generation feature map and artwork format definitions.

Sources:
  - libgpod ``itdb_device.c`` — itdb_device_supports_*() functions,
    ipod_info_table, artwork format tables
  - libgpod ``itdb_itunesdb.c`` — iTunesSD writer, mhbd version handling
  - Empirical: iPod Classic 2G, Nano 3G confirmed

This table captures every capability dimension that affects database
writing, artwork generation, or sync behaviour.  It is the single
authority for "what does this device support?" questions.
"""

from dataclasses import dataclass
from typing import Optional

from .checksum import ChecksumType


@dataclass(frozen=True)
class ArtworkFormat:
    """One artwork thumbnail size for a device generation.

    Attributes:
        format_id:    Ithmb correlation ID (e.g. 1055, 1060, 1061) — the
                      value stored in MHNIs and MHIFs in the ArtworkDB binary.
        width:        Pixel width
        height:       Pixel height
        row_bytes:    Bytes per row (often ``width * 2`` for RGB565)
        pixel_format: ``"RGB565_LE"`` (most iPods), ``"RGB565_LE_90"``
                      (rotated, Nano 4G), ``"RGB565_BE"`` (Mobile/Motorola)
        role:         ``"cover_small"``, ``"cover_large"``, ``"photo_full"``,
                      ``"tv_out"``, etc.
        description:  Human-readable label for logs / parser output.
    """
    format_id: int
    width: int
    height: int
    row_bytes: int
    pixel_format: str = "RGB565_LE"
    role: str = "cover"
    description: str = ""


@dataclass(frozen=True)
class DeviceCapabilities:
    """Per-generation device capability flags.

    Every (family, generation) pair maps to exactly one of these.  The
    flags drive decisions in the sync engine, iTunesDB writer, and
    ArtworkDB writer.

    All flags default to the *most common* value so that only deviations
    need to be specified in the lookup table.
    """

    # ── Database format ────────────────────────────────────────────────
    checksum: ChecksumType = ChecksumType.NONE
    is_shuffle: bool = False
    """If True, device uses iTunesSD (flat binary) instead of / in addition
    to iTunesDB.  Shadow DB version determines the iTunesSD format."""
    shadow_db_version: int = 0
    """0 = not a shuffle.  1 = iTunesSD v1 (Shuffle 1G/2G, 18-byte header,
    558-byte entries, big-endian).  2 = iTunesSD v2 (Shuffle 3G/4G,
    bdhs/hths/hphs chunk format, little-endian)."""
    supports_compressed_db: bool = False
    """If True, device expects iTunesCDB (zlib-compressed iTunesDB) and will
    generate an empty iTunesDB alongside it.  Nano 5G/6G/7G only."""

    # ── Media type support ─────────────────────────────────────────────
    supports_video: bool = False
    """Device can play video files (mediatype & VIDEO != 0)."""
    supports_podcast: bool = True
    """Device supports podcast mhsd types (type 3).  False only for
    very early iPods (1G–3G) and iPod Mobile."""
    supports_gapless: bool = False
    """Device honours gapless playback fields (pregap, postgap,
    samplecount, gapless_data, gapless_track_flag).  Introduced with
    iPod Video 5.5G (Late 2006)."""

    # ── Artwork ────────────────────────────────────────────────────────
    supports_artwork: bool = True
    """Device has an ArtworkDB and .ithmb files for album art."""
    supports_photo: bool = False
    """Device has additional photo artwork formats (for photo viewer)."""
    supports_chapter_image: bool = False
    """Device has chapter image artwork formats (for enhanced podcasts)."""
    supports_sparse_artwork: bool = False
    """Artwork can be written in sparse mode (Nano 3G+, Classic, Touch)."""
    supports_alac: bool = True
    """Device supports Apple Lossless (ALAC) audio playback.
    False for iPod 1G–3G and Mini 1G (pre-firmware-update era hardware that
    received ALAC support only from 4th Gen / Photo / Mini 2G onwards)."""
    cover_art_formats: tuple[ArtworkFormat, ...] = ()
    """Supported cover-art thumbnail sizes.  Empty means no artwork."""

    # ── Storage layout ─────────────────────────────────────────────────
    music_dirs: int = 20
    """Number of ``Fxx`` directories under ``iPod_Control/Music/``.
    Varies 0–50 depending on model and storage capacity."""

    # ── SQLite database ────────────────────────────────────────────────
    uses_sqlite_db: bool = False
    """If True, device uses SQLite databases in
    ``iTunes Library.itlp/`` instead of (or alongside) binary
    iTunesDB/iTunesCDB.  The firmware on Nano 6G/7G reads the SQLite
    databases and ignores iTunesCDB completely."""

    # ── Writer parameters ──────────────────────────────────────────────
    db_version: int = 0x30
    """iTunesDB version to write in mhbd header.  Older iPods need
    lower values (0x0c for Shuffle 1G/2G, 0x13 for pre-Classic)."""
    byte_order: str = "le"
    """Byte order for database writing.  ``"le"`` for almost all models.
    ``"be"`` for iPod Mobile (Motorola ROKR/SLVR/RAZR)."""

    # ── Screen / display ───────────────────────────────────────────────
    has_screen: bool = True
    """Device has a display.  Shuffles have no screen."""

    # ── Video encoding limits ──────────────────────────────────────────
    max_video_width: int = 0
    """Maximum H.264 decode width (pixels).  0 = no video support.
    This is the firmware decode ceiling, not the screen resolution —
    the device downscales to fit its screen."""
    max_video_height: int = 0
    """Maximum H.264 decode height (pixels).  0 = no video support."""
    max_video_fps: int = 30
    """Maximum frame rate for H.264 decode (fps).  All video-capable iPods
    support 30 fps; PAL-resolution Nano 7G content is typically 25 fps but
    30 fps playback is still supported."""
    max_video_bitrate: int = 0
    """Hard bitrate ceiling for H.264 decode (kbps).  0 = no explicit cap
    (quality-controlled by CRF only).  Non-zero values enforce a -maxrate
    flag in ffmpeg.
    Nano 3G/4G use Baseline Profile Level 1.3, capped at 768 kbps by spec."""
    h264_level: str = "3.0"
    """H.264 Baseline Profile level to target when encoding video.
    Most iPods support Level 3.0.  iPod Classic supports 3.1.
    Nano 3G/4G are limited to Level 1.3 by their hardware decoder."""


# ──────────────────────────────────────────────────────────────────────────
# Cover-art format sets — ithmb correlation IDs
# ──────────────────────────────────────────────────────────────────────────

_ART_PHOTO = (
    ArtworkFormat(1017, 56, 56, 112, "RGB565_LE", "cover_small", "Photo album art small"),
    ArtworkFormat(1016, 140, 140, 280, "RGB565_LE", "cover_large", "Photo album art large"),
)

_ART_NANO_1G2G = (
    ArtworkFormat(1031, 42, 42, 84, "RGB565_LE", "cover_small", "Nano album art small"),
    ArtworkFormat(1027, 100, 100, 200, "RGB565_LE", "cover_large", "Nano album art large"),
)

_ART_VIDEO = (
    ArtworkFormat(1028, 100, 100, 200, "RGB565_LE", "cover_small", "Video album art small"),
    ArtworkFormat(1029, 200, 200, 400, "RGB565_LE", "cover_large", "Video album art large"),
)

_ART_CLASSIC = (
    ArtworkFormat(1061, 56, 56, 112, "RGB565_LE", "cover_small", "Classic album art small"),
    ArtworkFormat(1055, 128, 128, 256, "RGB565_LE", "cover_medium", "Classic album art medium"),
    ArtworkFormat(1060, 320, 320, 640, "RGB565_LE", "cover_large", "Classic album art large"),
)

_ART_NANO_4G = (
    ArtworkFormat(1071, 240, 240, 480, "RGB565_LE", "cover_large", "Nano 4G album art large"),
    ArtworkFormat(1074, 50, 50, 100, "RGB565_LE", "cover_xsmall", "Nano 4G album art tiny"),
    ArtworkFormat(1078, 80, 80, 160, "RGB565_LE", "cover_small", "Nano 4G/5G album art small"),
)

_ART_NANO_5G = (
    ArtworkFormat(1073, 240, 240, 480, "RGB565_LE", "cover_large", "Nano 5G album art large"),
    ArtworkFormat(1056, 128, 128, 256, "RGB565_LE", "cover_medium", "Nano 5G album art medium"),
    ArtworkFormat(1078, 80, 80, 160, "RGB565_LE", "cover_small", "Nano 4G/5G album art small"),
    ArtworkFormat(1074, 50, 50, 100, "RGB565_LE", "cover_xsmall", "Nano 4G/5G album art tiny"),
)

_ART_NANO_6G = (
    ArtworkFormat(1073, 240, 240, 480, "RGB565_LE", "cover_large", "Nano 6G album art large"),
    ArtworkFormat(1085, 88, 88, 176, "RGB565_LE", "cover_medium", "Nano 6G album art medium"),
    ArtworkFormat(1089, 58, 58, 116, "RGB565_LE", "cover_small", "Nano 6G album art small"),
    ArtworkFormat(1074, 50, 50, 100, "RGB565_LE", "cover_xsmall", "Nano 6G album art tiny"),
)


# ──────────────────────────────────────────────────────────────────────────
# The master capabilities table
# ──────────────────────────────────────────────────────────────────────────

_FAMILY_GEN_CAPABILITIES: dict[tuple[str, str], DeviceCapabilities] = {

    # ── iPod 1G–3G: earliest models, no podcast, no gapless ───────────
    ("iPod", "1st Gen"): DeviceCapabilities(
        supports_podcast=False,
        supports_artwork=False,
        supports_alac=False,
        has_screen=True,
        music_dirs=20,
        db_version=0x13,
    ),
    ("iPod", "2nd Gen"): DeviceCapabilities(
        supports_podcast=False,
        supports_artwork=False,
        supports_alac=False,
        has_screen=True,
        music_dirs=20,
        db_version=0x13,
    ),
    ("iPod", "3rd Gen"): DeviceCapabilities(
        supports_podcast=False,
        supports_artwork=False,
        supports_alac=False,
        has_screen=True,
        music_dirs=20,
        db_version=0x13,
    ),

    # ── iPod 4G (Click Wheel): first with podcast support ─────────────
    ("iPod", "4th Gen"): DeviceCapabilities(
        supports_artwork=False,
        music_dirs=20,
        db_version=0x13,
    ),

    # ── iPod U2 Special Edition (4th Gen hardware) ────────────────────
    ("iPod U2", "4th Gen"): DeviceCapabilities(
        supports_artwork=False,
        music_dirs=20,
        db_version=0x13,
    ),

    # ── iPod Photo (Color Display) ────────────────────────────────────
    ("iPod Photo", "4th Gen"): DeviceCapabilities(
        supports_artwork=True,
        supports_photo=True,
        cover_art_formats=_ART_PHOTO,
        music_dirs=20,
        db_version=0x13,
    ),

    # ── iPod Video 5th Gen ────────────────────────────────────────────
    ("iPod Video", "5th Gen"): DeviceCapabilities(
        supports_video=True,
        supports_artwork=True,
        supports_photo=True,
        cover_art_formats=_ART_VIDEO,
        music_dirs=20,
        db_version=0x19,
        max_video_width=640,
        max_video_height=480,
    ),

    # ── iPod Video 5.5th Gen — first with gapless playback ───────────
    ("iPod Video", "5.5th Gen"): DeviceCapabilities(
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        cover_art_formats=_ART_VIDEO,
        music_dirs=20,
        db_version=0x19,
        max_video_width=640,
        max_video_height=480,
    ),

    # ── iPod Video U2 editions ────────────────────────────────────────
    ("iPod Video U2", "5th Gen"): DeviceCapabilities(
        supports_video=True,
        supports_artwork=True,
        supports_photo=True,
        cover_art_formats=_ART_VIDEO,
        music_dirs=20,
        db_version=0x19,
        max_video_width=640,
        max_video_height=480,
    ),
    ("iPod Video U2", "5.5th Gen"): DeviceCapabilities(
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        cover_art_formats=_ART_VIDEO,
        music_dirs=20,
        db_version=0x19,
        max_video_width=640,
        max_video_height=480,
    ),

    # ── iPod Classic (all gens): HASH58, gapless, video ───────────────
    ("iPod Classic", "1st Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASH58,
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        supports_chapter_image=True,
        supports_sparse_artwork=True,
        cover_art_formats=_ART_CLASSIC,
        music_dirs=50,
        db_version=0x30,
        max_video_width=640,
        max_video_height=480,
    ),
    ("iPod Classic", "2nd Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASH58,
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        supports_chapter_image=True,
        supports_sparse_artwork=True,
        cover_art_formats=_ART_CLASSIC,
        music_dirs=50,
        db_version=0x30,
        max_video_width=640,
        max_video_height=480,
    ),
    ("iPod Classic", "3rd Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASH58,
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        supports_chapter_image=True,
        supports_sparse_artwork=True,
        cover_art_formats=_ART_CLASSIC,
        music_dirs=50,
        db_version=0x30,
        max_video_width=640,
        max_video_height=480,
    ),

    # ── iPod Mini ─────────────────────────────────────────────────────
    ("iPod Mini", "1st Gen"): DeviceCapabilities(
        supports_artwork=False,
        supports_alac=False,
        music_dirs=6,
        db_version=0x13,
    ),
    ("iPod Mini", "2nd Gen"): DeviceCapabilities(
        supports_artwork=False,
        music_dirs=6,
        db_version=0x13,
    ),

    # ── iPod Nano 1G/2G ──────────────────────────────────────────────
    ("iPod Nano", "1st Gen"): DeviceCapabilities(
        supports_artwork=True,
        supports_photo=True,
        cover_art_formats=_ART_NANO_1G2G,
        music_dirs=14,
        db_version=0x13,
    ),
    ("iPod Nano", "2nd Gen"): DeviceCapabilities(
        supports_artwork=True,
        supports_photo=True,
        cover_art_formats=_ART_NANO_1G2G,
        music_dirs=14,
        db_version=0x13,
    ),

    # ── iPod Nano 3G ("Fat"): first Nano with video, HASH58 ──────────
    ("iPod Nano", "3rd Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASH58,
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        supports_sparse_artwork=True,
        cover_art_formats=_ART_CLASSIC,
        music_dirs=20,
        db_version=0x30,
        max_video_width=320,
        max_video_height=240,
        max_video_bitrate=768,
        h264_level="1.3",
    ),

    # ── iPod Nano 4G: HASH58 ─────────────────────────────────────────
    ("iPod Nano", "4th Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASH58,
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        supports_chapter_image=True,
        supports_sparse_artwork=True,
        cover_art_formats=_ART_NANO_4G,
        music_dirs=20,
        db_version=0x30,
        max_video_width=480,
        max_video_height=320,
        max_video_bitrate=768,
        h264_level="1.3",
    ),

    # ── iPod Nano 5G: HASH72, compressed DB + SQLite ─────────────────
    ("iPod Nano", "5th Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASH72,
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_photo=True,
        supports_sparse_artwork=True,
        supports_compressed_db=True,
        uses_sqlite_db=True,
        cover_art_formats=_ART_NANO_5G,
        music_dirs=14,
        db_version=0x30,
        max_video_width=640,
        max_video_height=480,
    ),

    # ── iPod Nano 6G: HASHAB, no video ───────────────────────────────
    ("iPod Nano", "6th Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASHAB,
        supports_video=False,
        supports_gapless=True,
        supports_artwork=True,
        supports_sparse_artwork=True,
        supports_compressed_db=True,
        uses_sqlite_db=True,
        cover_art_formats=_ART_NANO_6G,
        music_dirs=20,
        db_version=0x30,
    ),

    # ── iPod Nano 7G: HASHAB, video returns ──────────────────────────
    ("iPod Nano", "7th Gen"): DeviceCapabilities(
        checksum=ChecksumType.HASHAB,
        supports_video=True,
        supports_gapless=True,
        supports_artwork=True,
        supports_sparse_artwork=True,
        supports_compressed_db=True,
        uses_sqlite_db=True,
        cover_art_formats=_ART_NANO_6G,
        music_dirs=20,
        db_version=0x30,
        max_video_width=720,
        max_video_height=576,
    ),

    # ── iPod Shuffle 1G ──────────────────────────────────────────────
    ("iPod Shuffle", "1st Gen"): DeviceCapabilities(
        is_shuffle=True,
        shadow_db_version=1,
        supports_podcast=True,
        supports_artwork=False,
        has_screen=False,
        music_dirs=3,
        db_version=0x0c,
    ),

    # ── iPod Shuffle 2G ──────────────────────────────────────────────
    ("iPod Shuffle", "2nd Gen"): DeviceCapabilities(
        is_shuffle=True,
        shadow_db_version=1,
        supports_podcast=True,
        supports_artwork=False,
        has_screen=False,
        music_dirs=3,
        db_version=0x13,
    ),

    # ── iPod Shuffle 3G ──────────────────────────────────────────────
    ("iPod Shuffle", "3rd Gen"): DeviceCapabilities(
        is_shuffle=True,
        shadow_db_version=2,
        supports_podcast=True,
        supports_artwork=False,
        has_screen=False,
        music_dirs=3,
        db_version=0x19,
    ),

    # ── iPod Shuffle 4G ──────────────────────────────────────────────
    ("iPod Shuffle", "4th Gen"): DeviceCapabilities(
        is_shuffle=True,
        shadow_db_version=2,
        supports_podcast=True,
        supports_artwork=False,
        has_screen=False,
        music_dirs=3,
        db_version=0x19,
    ),
}


def capabilities_for_family_gen(
    family: str,
    generation: str,
) -> Optional[DeviceCapabilities]:
    """Return the device capabilities for a (family, generation) pair.

    If the exact pair is not found but *generation* is empty/unknown,
    checks whether all known generations of *family* share identical
    capabilities and returns those.

    Returns ``None`` if the pair is not in the lookup table and the
    family-level fallback is ambiguous.
    """
    caps = _FAMILY_GEN_CAPABILITIES.get((family, generation))
    if caps is not None:
        return caps

    if family and not generation:
        family_caps = [
            c for (f, _g), c in _FAMILY_GEN_CAPABILITIES.items()
            if f == family
        ]
        if family_caps and all(c == family_caps[0] for c in family_caps):
            return family_caps[0]

    return None


def checksum_type_for_family_gen(
    family: str,
    generation: str,
) -> Optional[ChecksumType]:
    """Return the checksum type for a (family, generation) pair.

    Derives the answer from ``_FAMILY_GEN_CAPABILITIES``.  If the exact
    (family, generation) pair is not found but *generation* is empty/unknown,
    checks whether all known generations of *family* share the same checksum
    type and returns it.

    Returns ``None`` if the pair is not in the lookup table and the family-
    level fallback is ambiguous.
    """
    caps = _FAMILY_GEN_CAPABILITIES.get((family, generation))
    if caps is not None:
        return caps.checksum

    if family and not generation:
        family_checksums = {
            c.checksum
            for (f, _g), c in _FAMILY_GEN_CAPABILITIES.items()
            if f == family
        }
        if len(family_checksums) == 1:
            return family_checksums.pop()

    return None
