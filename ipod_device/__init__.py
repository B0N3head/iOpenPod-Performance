"""
ipod_device — unified iPod device identification & management package.

Re-exports device-identification and model-capability APIs that were
historically spread across multiple legacy modules.
"""

# flake8: noqa: F401

# ── checksum ─────────────────────────────────────────────────────────
from .checksum import (
    ChecksumType,
    CHECKSUM_MHBD_SCHEME,
    MHBD_SCHEME_TO_CHECKSUM,
)

# ── capabilities ─────────────────────────────────────────────────────
from .capabilities import (
    ArtworkFormat,
    DeviceCapabilities,
    capabilities_for_family_gen,
    checksum_type_for_family_gen,
)

# ── artwork ──────────────────────────────────────────────────────────
from .artwork import (
    ITHMB_FORMAT_MAP,
    ITHMB_SIZE_MAP,
    ithmb_formats_for_device,
    photo_formats_for_device,
)

# ── models ───────────────────────────────────────────────────────────
from .models import (
    IPOD_MODELS,
    USB_PID_TO_MODEL,
    IPOD_USB_PIDS,
    SERIAL_LAST3_TO_MODEL,
)

# ── lookup ───────────────────────────────────────────────────────────
from .lookup import (
    extract_model_number,
    get_model_info,
    get_friendly_model_name,
    lookup_by_serial,
    infer_generation,
)

# ── images ───────────────────────────────────────────────────────────
from .images import (
    COLOR_MAP,
    MODEL_IMAGE,
    FAMILY_FALLBACK,
    GENERIC_IMAGE,
    IMAGE_COLORS,
    color_for_image,
    resolve_image_filename,
    image_for_model,
)

# ── info (device_info) ───────────────────────────────────────────────
from .info import (
    DeviceInfo,
    get_current_device,
    set_current_device,
    clear_current_device,
    detect_checksum_type,
    get_firewire_id,
    enrich,
    resolve_itdb_path,
    itdb_write_filename,
    read_sysinfo,
    generate_library_id,
)

# ── authority ────────────────────────────────────────────────────────
from .authority import (
    SOURCE_RANK,
    SYSINFO_FIELDS,
    AUTHORITY_FILENAME,
    check_authority_coverage,
    update_sysinfo,
    read_authority,
)

# ── vpd_libusb ───────────────────────────────────────────────────────
from .vpd_libusb import (
    query_ipod_vpd as usb_query_ipod_vpd,
    query_all_ipods as usb_query_all_ipods,
    write_sysinfo as usb_write_sysinfo,
    identify_via_vpd,
)

# ── vpd_iokit is macOS-only and raises ImportError on other platforms,
#    so we don't import it at package level.  Import directly:
#        from ipod_device.vpd_iokit import query_ipod_vpd

# ── scanner (GUI/device_scanner) ────────────────────────────────────
from .scanner import scan_for_ipods
