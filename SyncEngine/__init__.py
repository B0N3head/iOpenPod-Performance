"""
SyncEngine - Bridge between PC media library and iPod

Core components:
- PCLibrary: Scans PC media folder, extracts metadata
- FingerprintDiffEngine: Computes sync plan using acoustic fingerprints
- SyncExecutor: Executes sync plan (copy, transcode, update mapping)
- MappingManager: Tracks fingerprint → list[db_id] relationships
- Transcoder: Converts non-iPod formats (FLAC, etc.) to ALAC/AAC

"""

from .pc_library import PCLibrary, PCTrack
from .fingerprint_diff_engine import (
    FingerprintDiffEngine,
    SyncAction,
    SyncPlan,
    SyncItem,
    StorageSummary,
)
from .sync_executor import SyncExecutor, SyncResult, SyncProgress
from .audio_fingerprint import (
    compute_fingerprint,
    read_fingerprint,
    write_fingerprint,
    get_or_compute_fingerprint,
    is_fpcalc_available,
)
from .mapping import MappingManager, MappingFile, TrackMapping
from .integrity import check_integrity, IntegrityReport
from .itunes_prefs import (
    read_prefs,
    protect_from_itunes,
    check_library_owner,
    generate_library_id,
    ITunesPrefs,
    DeviceTotals,
    SyncHistoryEntry,
)
from .transcoder import (
    transcode,
    needs_transcoding,
    is_ffmpeg_available,
    TranscodeTarget,
    TranscodeResult,
)
from ._formats import IPOD_NATIVE_FORMATS
from .transcode_cache import TranscodeCache, CachedFile, CacheIndex
from .backup_manager import BackupManager, SnapshotInfo, BackupProgress, get_device_identifier, get_device_display_name
from .eta import ETATracker
from .spl_evaluator import spl_update, spl_update_from_parsed, spl_update_all
from .photos import (
    PhotoDB,
    PhotoAlbum,
    PhotoEntry,
    PCPhotoLibrary,
    PhotoEditState,
    PhotoSyncPlan,
    scan_pc_photos,
    read_photo_db,
    build_photo_sync_plan,
    apply_photo_sync_plan,
    load_photo_preview,
)

__all__ = [
    # PC Library
    "PCLibrary",
    "PCTrack",
    # Fingerprint-based sync (primary)
    "FingerprintDiffEngine",
    "SyncAction",
    "SyncPlan",
    "SyncItem",
    "StorageSummary",
    # Sync execution
    "SyncExecutor",
    "SyncResult",
    "SyncProgress",
    # Audio fingerprinting
    "compute_fingerprint",
    "read_fingerprint",
    "write_fingerprint",
    "get_or_compute_fingerprint",
    "is_fpcalc_available",
    # Mapping
    "MappingManager",
    "MappingFile",
    "TrackMapping",
    # Integrity
    "check_integrity",
    "IntegrityReport",
    # iTunes Prefs
    "read_prefs",
    "protect_from_itunes",
    "check_library_owner",
    "generate_library_id",
    "ITunesPrefs",
    "DeviceTotals",
    "SyncHistoryEntry",
    # Transcoding
    "transcode",
    "needs_transcoding",
    "is_ffmpeg_available",
    "TranscodeTarget",
    "TranscodeResult",
    "IPOD_NATIVE_FORMATS",
    # Transcode cache
    "TranscodeCache",
    "CachedFile",
    "CacheIndex",
    # Backup manager
    "BackupManager",
    "SnapshotInfo",
    "BackupProgress",
    "get_device_identifier",
    "get_device_display_name",
    # ETA tracking
    "ETATracker",
    # Smart playlist evaluator
    "spl_update",
    "spl_update_from_parsed",
    "spl_update_all",
    # Photos
    "PhotoDB",
    "PhotoAlbum",
    "PhotoEntry",
    "PCPhotoLibrary",
    "PhotoEditState",
    "PhotoSyncPlan",
    "scan_pc_photos",
    "read_photo_db",
    "build_photo_sync_plan",
    "apply_photo_sync_plan",
    "load_photo_preview",
]
