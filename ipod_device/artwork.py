"""Artwork lookups backed by the canonical format registry."""

from .artwork_presets import (
    ARTWORK_FORMATS_BY_ID,
    ArtworkFormat,
)
from .capabilities import capabilities_for_family_gen


ITHMB_FORMAT_MAP = ARTWORK_FORMATS_BY_ID
"""Fallback lookup of ithmb correlation ID -> ``ArtworkFormat``.

Apple reused some correlation IDs for different device families, so this map
is intentionally only a legacy/default lookup. Device-aware code should use
``cover_art_format_definitions_for_device`` or
``resolve_cover_art_format_definitions`` instead.
"""

ITHMB_SIZE_MAP: dict[int, ArtworkFormat] = {}
"""Fallback lookup: byte size -> ``ArtworkFormat``."""
for _af in ITHMB_FORMAT_MAP.values():
    _byte_size = _af.row_bytes * _af.height
    if _byte_size > 0 and _byte_size not in ITHMB_SIZE_MAP:
        ITHMB_SIZE_MAP[_byte_size] = _af


def ithmb_formats_for_device(
    family: str,
    generation: str,
    *,
    capacity: str | None = None,
    model_number: str | None = None,
) -> dict[int, tuple[int, int]]:
    """Return ``{correlation_id: (width, height)}`` for a device's cover art."""
    definitions = cover_art_format_definitions_for_device(
        family,
        generation,
        capacity=capacity,
        model_number=model_number,
    )
    return {fid: (af.width, af.height) for fid, af in definitions.items()}


def _format_dict(formats: tuple[ArtworkFormat, ...]) -> dict[int, ArtworkFormat]:
    return {af.format_id: af for af in formats}


def cover_art_format_definitions_for_device(
    family: str,
    generation: str,
    *,
    capacity: str | None = None,
    model_number: str | None = None,
) -> dict[int, ArtworkFormat]:
    """Return rich, device-specific cover-art format definitions.

    This preserves device-specific meanings for reused IDs, such as Nano 7G
    ``1015``/``1016`` and Classic 1G 80GB ``1044``.
    """

    caps = capabilities_for_family_gen(
        family,
        generation or "",
        capacity=capacity,
        model_number=model_number,
    )
    if caps is None or not caps.supports_artwork:
        return {}
    return _format_dict(caps.cover_art_formats)


def _resolve_observed_format(
    format_id: int,
    width: int,
    height: int,
    preferred_defs: dict[int, ArtworkFormat],
) -> ArtworkFormat:
    """Choose the best rich definition for an observed ``id -> dimensions``."""
    for candidate in (
        preferred_defs.get(format_id),
        ARTWORK_FORMATS_BY_ID.get(format_id),
    ):
        if candidate is None:
            continue
        if int(candidate.width) == int(width) and int(candidate.height) == int(height):
            return candidate

    return ArtworkFormat(
        int(format_id),
        int(width),
        int(height),
        int(width) * 2,
        "RGB565_LE",
        "cover",
        f"Device artwork format {format_id}",
    )


def resolve_cover_art_format_definitions(
    family: str = "",
    generation: str = "",
    *,
    capacity: str | None = None,
    model_number: str | None = None,
    observed_formats: dict[int, tuple[int, int]] | None = None,
) -> dict[int, ArtworkFormat]:
    """Resolve the authoritative cover-art definitions for a device.

    ``observed_formats`` usually comes from SysInfoExtended or an existing
    ArtworkDB. When present, its ID list is treated as authoritative while the
    model profile supplies the richer pixel-format/role metadata for IDs whose
    dimensions match.
    """
    preferred_defs = cover_art_format_definitions_for_device(
        family,
        generation,
        capacity=capacity,
        model_number=model_number,
    )

    if observed_formats:
        resolved: dict[int, ArtworkFormat] = {}
        for fid, dims in observed_formats.items():
            width, height = dims
            resolved[int(fid)] = _resolve_observed_format(
                int(fid),
                int(width),
                int(height),
                preferred_defs,
            )
        return resolved

    return preferred_defs


def resolve_cover_art_format_definitions_for_device(device) -> dict[int, ArtworkFormat]:
    """Resolve cover-art definitions from a ``DeviceInfo``-like object."""
    if device is None:
        return {}

    return resolve_cover_art_format_definitions(
        getattr(device, "model_family", "") or "",
        getattr(device, "generation", "") or "",
        capacity=getattr(device, "capacity", ""),
        model_number=getattr(device, "model_number", ""),
        observed_formats=getattr(device, "artwork_formats", None) or None,
    )


def photo_formats_for_device(
    family: str,
    generation: str,
    *,
    capacity: str | None = None,
    model_number: str | None = None,
) -> dict[int, ArtworkFormat]:
    """Return device-specific photo ithmb formats.

    This is separate from cover-art formats because iPods keep slide-show/photo
    caches in the ``Photos`` hierarchy rather than ``ArtworkDB``. The per-device
    formats are sourced from ``DeviceCapabilities.photo_formats``.
    """

    caps = capabilities_for_family_gen(
        family,
        generation or "",
        capacity=capacity,
        model_number=model_number,
    )
    formats = caps.photo_formats if caps is not None else ()
    if not formats:
        return {}
    return {af.format_id: af for af in formats}
