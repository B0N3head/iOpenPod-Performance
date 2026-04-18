"""Ithmb artwork format lookup tables and device artwork queries."""

from .capabilities import (
    ArtworkFormat,
    _ART_CLASSIC,
    _ART_NANO_1G2G,
    _ART_NANO_6G,
    _ART_NANO_4G,
    _ART_NANO_5G,
    _ART_PHOTO,
    _ART_VIDEO,
    capabilities_for_family_gen,
)


_EXTRA_FORMATS = (
    # iPod Photo/Video photos & TV
    ArtworkFormat(1009, 42, 30, 84, "RGB565_LE", "photo_list", "Photo list thumbnail"),
    ArtworkFormat(1013, 220, 176, 440, "RGB565_BE_90", "photo_full", "Photo full screen (rotated)"),
    ArtworkFormat(1015, 130, 88, 260, "RGB565_LE", "photo_preview", "Photo/Video preview"),
    ArtworkFormat(1019, 720, 480, 1440, "UYVY", "tv_out", "Photo/Video NTSC TV output"),
    ArtworkFormat(1020, 220, 176, 440, "RGB565_BE_90", "photo_full", "Photo full screen (alt rotated)"),
    # iPod Nano 1G/2G photos
    ArtworkFormat(1023, 176, 132, 352, "RGB565_BE", "photo_full", "Nano full screen"),
    ArtworkFormat(1032, 42, 37, 84, "RGB565_LE", "photo_list", "Nano list thumbnail"),
    # iPod Nano 7G photos
    ArtworkFormat(1005, 80, 80, 160, "RGB565_LE", "photo_thumb", "Nano 7G photo thumbnail"),
    ArtworkFormat(1007, 480, 864, 960, "RGB565_LE", "photo_full", "Nano 7G photo full screen"),
    # iPod Video photos
    ArtworkFormat(1024, 320, 240, 640, "RGB565_LE", "photo_full", "Video full screen"),
    ArtworkFormat(1036, 50, 41, 100, "RGB565_LE", "photo_list", "Video list thumbnail"),
    # iPod Classic alternates & photos
    ArtworkFormat(1056, 128, 128, 256, "RGB565_LE", "cover_medium_alt", "Classic album art (alt)"),
    ArtworkFormat(1068, 128, 128, 256, "RGB565_LE", "cover_medium_alt", "Classic album art (alt 2)"),
    ArtworkFormat(1066, 64, 64, 128, "RGB565_LE", "photo_thumb", "Classic photo thumbnail"),
    ArtworkFormat(1067, 720, 480, 1080, "I420_LE", "tv_out", "Classic TV output (YUV)"),
    # iPod Nano 4G/5G alternates & photos
    ArtworkFormat(1084, 240, 240, 480, "RGB565_LE", "cover_large_alt", "Nano 4G album art (alt)"),
    ArtworkFormat(1079, 80, 80, 160, "RGB565_LE", "photo_thumb", "Nano 4G/5G photo thumbnail"),
    ArtworkFormat(1083, 320, 240, 640, "RGB565_LE", "photo_full", "Nano 4G photo full screen"),
    ArtworkFormat(1087, 384, 384, 768, "RGB565_LE", "photo_large", "Nano 5G photo large"),
    ArtworkFormat(1092, 80, 80, 160, "RGB565_LE", "photo_thumb", "Nano 6G photo thumbnail"),
    ArtworkFormat(1093, 512, 512, 1024, "RGB565_LE", "photo_full", "Nano 6G photo full screen"),
    # Legacy/mobile/touch formats documented in libgpod reverse-engineering
    ArtworkFormat(1081, 640, 480, 0, "JPEG", "photo_full", "JPEG photo format (experimental/legacy)"),
    ArtworkFormat(2002, 50, 50, 100, "RGB565_BE", "cover_small", "iPod Mobile cover art small"),
    ArtworkFormat(2003, 150, 150, 300, "RGB565_BE", "cover_large", "iPod Mobile cover art large"),
    ArtworkFormat(3001, 256, 256, 512, "REC_RGB555_LE", "cover_large", "iPod touch cover art large"),
    ArtworkFormat(3002, 128, 128, 256, "REC_RGB555_LE", "cover_medium", "iPod touch cover art medium"),
    ArtworkFormat(3003, 64, 64, 128, "REC_RGB555_LE", "cover_small", "iPod touch cover art small"),
    ArtworkFormat(3005, 320, 320, 640, "RGB555_LE", "cover_xlarge", "iPod touch cover art xlarge"),
)

ITHMB_FORMAT_MAP: dict[int, ArtworkFormat] = {}
"""Comprehensive lookup of ithmb correlation ID → `ArtworkFormat`."""
for _group in (_ART_PHOTO, _ART_NANO_1G2G, _ART_VIDEO, _ART_CLASSIC,
               _ART_NANO_4G, _ART_NANO_5G, _ART_NANO_6G, _EXTRA_FORMATS):
    for _af in _group:
        if _af.format_id not in ITHMB_FORMAT_MAP:
            ITHMB_FORMAT_MAP[_af.format_id] = _af

ITHMB_SIZE_MAP: dict[int, ArtworkFormat] = {}
"""Fallback lookup: byte size → `ArtworkFormat`."""
for _af in ITHMB_FORMAT_MAP.values():
    _byte_size = _af.row_bytes * _af.height
    if _byte_size > 0 and _byte_size not in ITHMB_SIZE_MAP:
        ITHMB_SIZE_MAP[_byte_size] = _af


def ithmb_formats_for_device(
    family: str,
    generation: str,
) -> dict[int, tuple[int, int]]:
    """Return ``{correlation_id: (width, height)}`` for a device's cover art."""
    caps = capabilities_for_family_gen(family, generation or "")
    if caps is None or not caps.supports_artwork:
        return {}
    return {af.format_id: (af.width, af.height) for af in caps.cover_art_formats}


def photo_formats_for_device(
    family: str,
    generation: str,
) -> dict[int, ArtworkFormat]:
    """Return device-specific photo ithmb formats.

    This is separate from cover-art formats because iPods keep slide-show/photo
    caches in the ``Photos`` hierarchy rather than ``ArtworkDB``. The per-device
    format IDs are sourced from ``DeviceCapabilities.photo_format_ids``.
    """
    caps = capabilities_for_family_gen(family, generation or "")
    format_ids = caps.photo_format_ids if caps is not None else ()
    if not format_ids:
        return {}
    result: dict[int, ArtworkFormat] = {}
    for format_id in format_ids:
        af = ITHMB_FORMAT_MAP.get(format_id)
        if af is not None:
            result[format_id] = af
    return result
