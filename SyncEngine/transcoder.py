"""
Transcoder — Convert audio/video files to iPod-compatible formats via FFmpeg.

Supported conversions:
  FLAC/WAV/AIFF  → ALAC (lossless) or AAC (if prefer_lossy setting is on)
  OGG/Opus/WMA   → AAC
  Video           → M4V (H.264 Baseline + stereo AAC)
  Native formats  → re-encoded only when they exceed iPod hardware limits

iPod hardware limits enforced on every output:
  Sample rate  ≤ 48 000 Hz
  Channels     ≤ 2 (stereo)
  Bit depth    ≤ 16-bit   (ALAC only — AAC/MP3 are inherently ≤16-bit)
"""

from ._formats import (
    IPOD_NATIVE_FORMATS,
    NON_NATIVE_LOSSLESS as _NON_NATIVE_LOSSLESS_EXTS,
    NON_NATIVE_LOSSY as _NON_NATIVE_LOSSY_EXTS,
    NON_NATIVE_VIDEO as _NON_NATIVE_VIDEO_EXTS,
)
import json as _json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Callable, Optional

logger = logging.getLogger(__name__)

# Suppress console flash on Windows
_SP_KWARGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

# ── iPod hardware limits ────────────────────────────────────────────────────

IPOD_MAX_SAMPLE_RATE = 48_000   # Hz
IPOD_MAX_CHANNELS = 2           # Stereo
IPOD_MAX_BIT_DEPTH = 16         # ALAC/WAV ceiling

# Fallback video limits when no device is connected.
_DEFAULT_VIDEO_W = 640
_DEFAULT_VIDEO_H = 480

# ── Format classification ───────────────────────────────────────────────────


class TranscodeTarget(Enum):
    """What codec to produce."""
    ALAC = "alac"
    AAC = "aac"
    VIDEO_H264 = "video_h264"
    COPY = "copy"


_OUTPUT_EXT: dict[TranscodeTarget, str] = {
    TranscodeTarget.ALAC: ".m4a",
    TranscodeTarget.AAC: ".m4a",
    TranscodeTarget.VIDEO_H264: ".m4v",
}

# Approximate stereo kbps for libfdk_aac -vbr 1..5 (midpoints of the per-channel
# ranges in the libfdk-aac docs, doubled for 2ch). Used only for size estimates.
_LIBFDK_VBR_KBPS: dict[int, int] = {1: 52, 2: 72, 3: 104, 4: 136, 5: 208}


@dataclass(frozen=True)
class TranscodeOptions:
    """User-configurable transcoding options for one operation."""

    ffmpeg_path: str = ""
    prefer_lossy: bool = False
    normalize_sample_rate: bool = False
    mono_for_spoken: bool = True
    smart_quality_by_type: bool = True
    video_crf: int = 23
    video_preset: str = "medium"
    aac_encoder: str = "auto"
    aac_mode: str = "cbr"
    aac_music_bitrate: int = 192
    aac_vbr_level: int = 4
    aac_spoken_bitrate: int = 64

    @classmethod
    def from_settings(cls, settings: object) -> "TranscodeOptions":
        return cls(
            ffmpeg_path=str(getattr(settings, "ffmpeg_path", "") or ""),
            prefer_lossy=bool(getattr(settings, "prefer_lossy", False)),
            normalize_sample_rate=bool(
                getattr(settings, "normalize_sample_rate", False)
            ),
            mono_for_spoken=bool(getattr(settings, "mono_for_spoken", True)),
            smart_quality_by_type=bool(
                getattr(settings, "smart_quality_by_type", True)
            ),
            video_crf=int(getattr(settings, "video_crf", 23) or 23),
            video_preset=str(getattr(settings, "video_preset", "medium") or "medium"),
            aac_encoder=str(getattr(settings, "aac_encoder", "auto") or "auto"),
            aac_mode=str(getattr(settings, "aac_mode", "cbr") or "cbr"),
            aac_music_bitrate=int(
                getattr(settings, "aac_music_bitrate", 192) or 192
            ),
            aac_vbr_level=int(getattr(settings, "aac_vbr_level", 4) or 4),
            aac_spoken_bitrate=int(
                getattr(settings, "aac_spoken_bitrate", 64) or 64
            ),
        )


@dataclass(frozen=True)
class TranscodePlan:
    """Resolved transcode policy for a source file.

    This is the shared decision object used by the transcoder, sync executor,
    and storage estimator so target selection and bitrate assumptions do not
    drift apart.
    """

    source_path: Path
    target: TranscodeTarget
    aac_quality: str
    effective_quality: str
    prefer_lossy: bool
    normalize_sample_rate: bool
    mono_for_spoken: bool
    smart_quality_by_type: bool
    video_crf: int
    video_preset: str
    video_max_width: int
    video_max_height: int
    video_max_fps: int
    video_max_bitrate_kbps: int
    video_h264_level: str
    aac_encoder: str = "aac"
    aac_mode: str = "cbr"
    aac_vbr_level: int = 4
    aac_music_bitrate: int = 192
    aac_spoken_bitrate: int = 64

    @property
    def is_spoken(self) -> bool:
        return self.effective_quality == "spoken"

    @property
    def output_extension(self) -> str:
        return _OUTPUT_EXT.get(self.target, self.source_path.suffix)

    @property
    def cache_target_format(self) -> str:
        if self.target == TranscodeTarget.ALAC:
            return "alac"
        if self.target == TranscodeTarget.AAC:
            return "aac"
        if self.target == TranscodeTarget.VIDEO_H264:
            return "m4v"
        return self.source_path.suffix.lstrip(".")

    @property
    def cache_bitrate_kbps(self) -> Optional[int]:
        if self.target == TranscodeTarget.AAC:
            return self._nominal_aac_bitrate(self.effective_quality)
        return None

    def estimate_output_size(self, *, source_size: int, duration_ms: int) -> int:
        """Estimate the post-transcode size in bytes.

        All estimates are a flat duration × nominal-CBR formula — no probing
        or sampling. Accuracy is "good enough for a storage bar"; the sync
        pipeline relies on exact disk sizes once files are written.
        """
        if self.target == TranscodeTarget.COPY:
            return source_size

        duration_seconds = duration_ms / 1000.0

        if self.target == TranscodeTarget.AAC:
            bitrate_kbps = self._estimated_aac_kbps()
            return int((duration_seconds * bitrate_kbps * 1000) / 8)

        if self.target == TranscodeTarget.ALAC:
            # WAV/AIFF → assume CD-quality PCM (1411 kbps) at ~55% ALAC ratio.
            # Other lossless inputs (FLAC etc.) are already compressed, so the
            # source size is the best no-probe estimate.
            suffix = self.source_path.suffix.lower()
            if suffix in {".wav", ".aif", ".aiff"} and duration_seconds > 0:
                return int((duration_seconds * 1411 * 1000 * 0.55) / 8)

            if source_size > 0:
                return source_size

            return int((duration_seconds * 900 * 1000) / 8)

        if self.target == TranscodeTarget.VIDEO_H264:
            return int((duration_seconds * self._estimated_video_kbps() * 1000) / 8)

        return source_size

    def _estimated_aac_kbps(self) -> int:
        """Rough CBR bitrate (kbps) for the AAC output, mode-aware.

        libfdk_aac in true VBR mode is driven by ``-vbr N`` and ignores the
        configured ``aac_music_bitrate``, so fall back to a per-level table.
        All other modes (cbr / abr / cvbr, and aac_at's vbr hint) target the
        configured bitrate closely enough for a storage estimate.
        """
        if self.is_spoken:
            return self._nominal_aac_bitrate(self.effective_quality)

        if self.aac_encoder == "libfdk_aac" and self.aac_mode == "vbr":
            # Approximate stereo kbps for libfdk_aac -vbr 1..5.
            return _LIBFDK_VBR_KBPS.get(self.aac_vbr_level, 144)

        return self._nominal_aac_bitrate(self.effective_quality)

    def _nominal_aac_bitrate(self, quality: str) -> int:
        return self.aac_spoken_bitrate if quality == "spoken" else self.aac_music_bitrate

    def _estimated_video_kbps(self) -> int:
        """Rough CBR bitrate (kbps) for H.264 output at the plan's CRF.

        Model: ``kbps ≈ W × H × fps × bpp``, with ``bpp ≈ 0.10`` at CRF 23,
        scaled by ``2^((23 − crf) / 6)`` (each 6-point CRF step halves or
        doubles bitrate). Clamped to the device bitrate cap.
        """
        width = self.video_max_width or _DEFAULT_VIDEO_W
        height = self.video_max_height or _DEFAULT_VIDEO_H
        fps = self.video_max_fps or _DEFAULT_VIDEO_FPS
        base_kbps = (width * height * fps * 0.10) / 1000.0
        crf_scale = 2 ** ((23 - self.video_crf) / 6)
        est = int(base_kbps * crf_scale)
        cap = self.video_max_bitrate_kbps or 2500
        return max(64, min(est, cap))


def resolve_transcode_plan(
    filepath: str | Path,
    *,
    aac_quality: Optional[str] = None,
    prefer_lossy: Optional[bool] = None,
    options: Optional[TranscodeOptions] = None,
) -> TranscodePlan:
    """Resolve the full transcode policy for *filepath*.

    This calls the same target-selection logic used by :func:`transcode` and
    captures the user settings that influence the output command.

    ``aac_quality`` is a legacy override hint.  Only ``"spoken"`` is treated
    specially; all other values are normalized to music quality.
    """
    source_path = Path(filepath)
    options = options or TranscodeOptions()
    if prefer_lossy is None:
        prefer_lossy = options.prefer_lossy

    target = get_transcode_target(source_path, prefer_lossy=prefer_lossy)
    normalize_sample_rate = options.normalize_sample_rate
    mono_for_spoken = options.mono_for_spoken
    smart_quality_by_type = options.smart_quality_by_type

    effective_quality = "spoken" if aac_quality == "spoken" else "normal"
    if smart_quality_by_type and target == TranscodeTarget.AAC:
        media_type = _probe_media_type(source_path)
        if media_type in _SPOKEN_STIK_VALUES:
            effective_quality = "spoken"

    aac_encoder_resolved = (
        _resolve_encoder(options.aac_encoder, options.ffmpeg_path)
        if target == TranscodeTarget.AAC
        else "aac"
    )

    max_width, max_height, max_fps, max_bitrate_kbps, h264_level = _get_video_caps()

    return TranscodePlan(
        source_path=source_path,
        target=target,
        aac_quality=aac_quality or "normal",
        effective_quality=effective_quality,
        prefer_lossy=prefer_lossy,
        normalize_sample_rate=normalize_sample_rate,
        mono_for_spoken=mono_for_spoken,
        smart_quality_by_type=smart_quality_by_type,
        video_crf=options.video_crf,
        video_preset=options.video_preset,
        video_max_width=max_width,
        video_max_height=max_height,
        video_max_fps=max_fps,
        video_max_bitrate_kbps=max_bitrate_kbps,
        video_h264_level=h264_level,
        aac_encoder=aac_encoder_resolved,
        aac_mode=options.aac_mode,
        aac_vbr_level=options.aac_vbr_level,
        aac_music_bitrate=options.aac_music_bitrate,
        aac_spoken_bitrate=options.aac_spoken_bitrate,
    )


# ── Result ──────────────────────────────────────────────────────────────────

@dataclass
class TranscodeResult:
    """Outcome of a single transcode / copy operation."""
    success: bool
    source_path: Path
    output_path: Optional[Path]
    target_format: TranscodeTarget
    was_transcoded: bool
    error_message: Optional[str] = None

    @property
    def ipod_format(self) -> str:
        if self.output_path:
            return self.output_path.suffix.lstrip(".")
        return self.source_path.suffix.lstrip(".")


def clear_caches() -> None:
    """Clear cached settings/binary lookups. Call at the start of each sync."""
    _find_ffprobe.cache_clear()
    _best_aac_encoder.cache_clear()


# ═══════════════════════════════════════════════════════════════════════════
# Binary discovery
# ═══════════════════════════════════════════════════════════════════════════

def find_ffmpeg(ffmpeg_path: Optional[str] = None) -> Optional[str]:
    """Locate ffmpeg (user setting → bundled → PATH → common dirs)."""
    if ffmpeg_path and Path(ffmpeg_path).is_file():
        return ffmpeg_path
    try:
        from .dependency_manager import get_bundled_ffmpeg
        bundled = get_bundled_ffmpeg()
        if bundled:
            return bundled
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    for p in (
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ):
        if Path(p).exists():
            return p
    return None


def is_ffmpeg_available(ffmpeg_path: Optional[str] = None) -> bool:
    return find_ffmpeg(ffmpeg_path) is not None


@lru_cache(maxsize=1)
def _find_ffprobe() -> Optional[str]:
    """Locate ffprobe (sibling of ffmpeg, then PATH)."""
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
        candidate = Path(ffmpeg).parent / name
        if candidate.exists():
            return str(candidate)
    return shutil.which("ffprobe")


@lru_cache(maxsize=8)
def available_aac_encoders(ffmpeg_path: Optional[str] = None) -> set[str]:
    """Return the set of AAC encoders exposed by the current ffmpeg build."""
    ffmpeg = find_ffmpeg(ffmpeg_path)
    if not ffmpeg:
        return set()
    try:
        r = subprocess.run(
            [ffmpeg, "-encoders"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=10, **_SP_KWARGS,
        )
        out = r.stdout
        available: set[str] = set()
        for encoder in ("libfdk_aac", "aac_at", "aac"):
            if f" {encoder} " in out:
                available.add(encoder)
        return available
    except Exception:
        return set()


@lru_cache(maxsize=8)
def _best_aac_encoder(ffmpeg_path: Optional[str] = None) -> str:
    """Return the best available AAC encoder.

    Preference: libfdk_aac (Fraunhofer) > aac_at (macOS AudioToolbox) > aac.
    """
    available = available_aac_encoders(ffmpeg_path)
    for encoder in ("libfdk_aac", "aac_at", "aac"):
        if encoder in available:
            logger.info("Using AAC encoder: %s", encoder)
            return encoder
    return "aac"


# ═══════════════════════════════════════════════════════════════════════════
# Probing
# ═══════════════════════════════════════════════════════════════════════════

def _run_ffprobe(args: list[str], timeout: int = 30) -> Optional[dict]:
    """Run ffprobe with *args*, return parsed JSON or None."""
    probe = _find_ffprobe()
    if not probe:
        return None
    try:
        r = subprocess.run(
            [probe, "-v", "quiet", "-print_format", "json", *args],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, **_SP_KWARGS,
        )
        if r.returncode == 0:
            return _json.loads(r.stdout)
    except Exception:
        pass
    return None


@dataclass(frozen=True)
class AudioProperties:
    """Probed audio-stream properties."""
    sample_rate: int = 0
    bits_per_sample: int = 0
    channels: int = 0
    codec_name: str = ""   # e.g. "aac", "mp3", "flac"
    profile: str = ""      # e.g. "LC", "HE-AAC", "HE-AACv2"
    probe_ok: bool = False  # False when ffprobe couldn't parse the file

    # AAC profiles the iPod can play — anything else is re-encoded
    _COMPATIBLE_AAC_PROFILES: ClassVar[frozenset[str]] = frozenset({
        "lc", "aac_low", "aac lc",
    })

    def exceeds_ipod_limits(self) -> bool:
        return (
            self.sample_rate > IPOD_MAX_SAMPLE_RATE
            or self.bits_per_sample > IPOD_MAX_BIT_DEPTH
            or self.channels > IPOD_MAX_CHANNELS
        )

    def is_incompatible_aac_profile(self) -> bool:
        """True if the stream is AAC but not a profile the iPod supports."""
        if self.codec_name.lower() != "aac":
            return False
        # Empty profile string means ffprobe couldn't determine it — treat as incompatible
        if not self.profile:
            return True
        return self.profile.lower() not in self._COMPATIBLE_AAC_PROFILES


def probe_audio(filepath: str | Path) -> AudioProperties:
    """Probe the first audio stream for sample rate, bit depth, channels, and codec."""
    info = _run_ffprobe([
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,bits_per_raw_sample,channels,codec_name,profile",
        str(filepath),
    ])
    if not info:
        return AudioProperties(probe_ok=False)
    streams = info.get("streams", [])
    if not streams:
        return AudioProperties(probe_ok=False)
    s = streams[0]
    return AudioProperties(
        sample_rate=int(s.get("sample_rate", 0)),
        bits_per_sample=int(s.get("bits_per_raw_sample", 0) or 0),
        channels=int(s.get("channels", 0)),
        codec_name=s.get("codec_name", ""),
        profile=s.get("profile", ""),
        probe_ok=True,
    )


def probe_video_needs_transcode(
    filepath: str | Path,
    ffprobe_path: Optional[str] = None,
) -> bool:
    """True if a video file needs re-encoding for iPod compatibility."""
    probe = ffprobe_path or _find_ffprobe()
    if not probe:
        return True

    max_w, max_h, max_fps, *_ = _get_video_caps()

    try:
        r = subprocess.run(
            [probe, "-v", "quiet", "-print_format", "json",
             "-show_streams", str(filepath)],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120, **_SP_KWARGS,
        )
        if r.returncode != 0:
            return True
        streams = _json.loads(r.stdout).get("streams", [])
    except Exception:
        return True

    video_ok = audio_ok = False
    for s in streams:
        ct = s.get("codec_type")
        if ct == "video":
            if s.get("codec_name", "").lower() != "h264":
                return True
            if "10" in s.get("pix_fmt", ""):
                return True
            if int(s.get("width", 9999)) > max_w:
                return True
            if int(s.get("height", 9999)) > max_h:
                return True
            # Check frame rate — r_frame_rate is a fraction string like "60/1"
            r_fr = s.get("r_frame_rate", "0/1")
            try:
                num, den = (int(x) for x in r_fr.split("/"))
                fps = num / den if den else 0
                if fps > max_fps + 0.5:   # 0.5 tolerance for rounding
                    return True
            except (ValueError, ZeroDivisionError):
                pass
            video_ok = True
        elif ct == "audio":
            if s.get("codec_name", "").lower() != "aac":
                return True
            if int(s.get("channels", 0)) > 2:
                return True
            audio_ok = True
    return not (video_ok and audio_ok)


def _probe_duration_us(filepath: str | Path) -> int:
    info = _run_ffprobe(["-show_format", str(filepath)], timeout=120)
    if not info:
        return 0
    try:
        return int(float(info.get("format", {}).get("duration", 0)) * 1_000_000)
    except (ValueError, TypeError):
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# Target resolution — "what should this file become?"
# ═══════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _read_prefer_lossy() -> bool:
    return False


@lru_cache(maxsize=1)
def _read_audio_settings() -> tuple[bool, bool, bool]:
    """Return ``(normalize_sample_rate, mono_for_spoken, smart_quality_by_type)``
    from settings, with safe defaults if settings are unavailable."""
    return False, True, True


# stik atom values that indicate spoken-word content.
# Apple stik table: 0=Movie, 1=Normal(Music), 2=Audiobook, 5=Whacked Bookmark,
# 6=Music Video, 9=Short Film, 10=TV Show, 11=Booklet, 14=Ringtone, 21=iTunes U,
# 23=Voice Memo, 24=iTunes Extras
_SPOKEN_STIK_VALUES: frozenset[int] = frozenset({2, 21})  # Audiobook, iTunes U


def _probe_media_type(filepath: str | Path) -> int:
    """Return the ``stik`` atom value from an MP4/M4A file, or -1 if absent/unreadable.

    stik values relevant here:
      2  = Audiobook
      21 = iTunes U / Podcast (many podcast encoders write 21)
    Additionally, the presence of a ``pcst`` (podcast) atom is checked as a fallback.
    """
    try:
        from mutagen.mp4 import MP4
        tags = MP4(str(filepath)).tags
        if tags is None:
            return -1
        stik = tags.get("stik")
        if stik:
            return int(stik[0])
        # Podcast fallback: pcst atom = True
        pcst = tags.get("pcst")
        if pcst and pcst[0]:
            return 21
    except Exception:
        pass
    return -1


_DEFAULT_VIDEO_FPS = 30
_DEFAULT_VIDEO_BITRATE = 0      # 0 = CRF-only, no hard bitrate cap
_DEFAULT_VIDEO_LEVEL = "3.0"


def _get_video_caps() -> tuple[int, int, int, int, str]:
    """Return ``(max_width, max_height, max_fps, max_bitrate_kbps, h264_level)``
    for the currently connected iPod.

    Falls back to ``640×480 / 30 fps / no bitrate cap / Level 3.0`` when no
    device is connected or the model is unrecognised.
    """
    try:
        from ipod_device import get_current_device
        from ipod_device import capabilities_for_family_gen
        dev = get_current_device()
        if dev and dev.model_family:
            caps = capabilities_for_family_gen(
                dev.model_family, dev.generation or "",
            )
            if caps and caps.max_video_width > 0:
                return (
                    caps.max_video_width,
                    caps.max_video_height,
                    caps.max_video_fps,
                    caps.max_video_bitrate,
                    caps.h264_level,
                )
    except Exception:
        pass
    return _DEFAULT_VIDEO_W, _DEFAULT_VIDEO_H, _DEFAULT_VIDEO_FPS, _DEFAULT_VIDEO_BITRATE, _DEFAULT_VIDEO_LEVEL


def _get_video_limits() -> tuple[int, int]:
    """Return ``(max_width, max_height)`` — kept for callers that only need
    the resolution.  Prefer ``_get_video_caps()`` for full device info."""
    w, h, *_ = _get_video_caps()
    return w, h


def _device_supports_alac() -> bool:
    """Return True if the connected iPod supports ALAC audio.

    Falls back to True (safe default) when no device is connected or
    the model is unrecognised — avoids unnecessary re-encodes.
    """
    try:
        from ipod_device import get_current_device
        from ipod_device import capabilities_for_family_gen
        dev = get_current_device()
        if dev and dev.model_family:
            caps = capabilities_for_family_gen(
                dev.model_family, dev.generation or "",
            )
            if caps is not None:
                return caps.supports_alac
    except Exception:
        pass
    return True


def get_transcode_target(
    filepath: str | Path,
    *,
    prefer_lossy: Optional[bool] = None,
) -> TranscodeTarget:
    """Determine the target format for *filepath*.

    Decision tree:
      1. Video → probe → VIDEO_H264 or COPY
      2. Lossless source → ALAC (or AAC if prefer_lossy)
      3. Lossy non-native → AAC
      4. Native → COPY, unless iPod limits are exceeded
         (hi-res sample rate / 24-bit / surround)
         or prefer_lossy wants to shrink a native ALAC
    """
    suffix = Path(filepath).suffix.lower()

    # ── Non-native video — always transcode ─────────────────────────────
    if suffix in _NON_NATIVE_VIDEO_EXTS:
        return TranscodeTarget.VIDEO_H264

    if prefer_lossy is None:
        prefer_lossy = _read_prefer_lossy()

    # ── Non-native audio ────────────────────────────────────────────────
    if suffix in _NON_NATIVE_LOSSLESS_EXTS:
        if prefer_lossy or not _device_supports_alac():
            return TranscodeTarget.AAC
        return TranscodeTarget.ALAC
    if suffix in _NON_NATIVE_LOSSY_EXTS:
        return TranscodeTarget.AAC

    # ── Native formats ──────────────────────────────────────────────────
    if suffix in IPOD_NATIVE_FORMATS:
        # Native video — probe codec compatibility
        if suffix in {".mp4", ".m4v"}:
            return (TranscodeTarget.VIDEO_H264
                    if probe_video_needs_transcode(filepath)
                    else TranscodeTarget.COPY)

        # Native audio — probe for iPod limits and codec compatibility
        props = probe_audio(filepath)

        # Probe failed: ffprobe couldn't parse this file.
        # MP3 is safe to copy blind; M4A/AAC could be HE-AAC so re-encode.
        if not props.probe_ok:
            if suffix in {".m4a", ".m4b", ".aac"}:
                logger.warning("TRANSCODE: could not probe %s — re-encoding to AAC as safe fallback",
                               Path(filepath).name)
                return TranscodeTarget.AAC
            logger.warning("TRANSCODE: could not probe %s — copying as-is", Path(filepath).name)
            return TranscodeTarget.COPY

        if props.exceeds_ipod_limits():
            if suffix in {".m4a", ".m4b"} and not prefer_lossy and _device_supports_alac():
                return TranscodeTarget.ALAC
            return TranscodeTarget.AAC

        # HE-AAC v1/v2 — iPod only supports AAC-LC; re-encode to LC
        if props.is_incompatible_aac_profile():
            logger.info("TRANSCODE: %s has incompatible AAC profile %r — re-encoding to AAC-LC",
                        Path(filepath).name, props.profile)
            return TranscodeTarget.AAC

        # User wants to shrink native ALAC → AAC
        # (bits_per_sample ≥ 16 distinguishes ALAC from AAC which reports 0)
        if prefer_lossy and suffix in {".m4a", ".m4b"} and props.bits_per_sample >= 16:
            return TranscodeTarget.AAC

        # Device doesn't support ALAC: transcode native ALAC → AAC
        if (suffix in {".m4a", ".m4b"} and props.bits_per_sample >= 16
                and not _device_supports_alac()):
            return TranscodeTarget.AAC

        return TranscodeTarget.COPY

    # Unknown extension — AAC is the safest bet
    return TranscodeTarget.AAC


def needs_transcoding(
    filepath: str | Path,
    *,
    prefer_lossy: Optional[bool] = None,
) -> bool:
    """True if the file needs any conversion before it can go on iPod."""
    return get_transcode_target(filepath, prefer_lossy=prefer_lossy) != TranscodeTarget.COPY


# ═══════════════════════════════════════════════════════════════════════════
# AAC quality presets
# ═══════════════════════════════════════════════════════════════════════════

def quality_to_nominal_bitrate(
    quality: str,
    options: Optional[TranscodeOptions] = None,
) -> int:
    """Return the nominal bitrate (kbps) for display / cache-key purposes.

    Reads the user's configured bitrates from settings.  Falls back to
    safe defaults if settings are unavailable.
    """
    options = options or TranscodeOptions()
    if quality == "spoken":
        return options.aac_spoken_bitrate
    return options.aac_music_bitrate


def _resolve_encoder(
    encoder_pref: str,
    ffmpeg_path: Optional[str] = None,
) -> str:
    """Resolve 'auto' to the best available encoder name.

    When a specific encoder is requested but unavailable, falls back to
    the best available encoder rather than failing silently.
    """
    if encoder_pref == "auto":
        return _best_aac_encoder(ffmpeg_path)
    # Validate: if the requested encoder isn't available, fall back to auto
    available = available_aac_encoders(ffmpeg_path)
    if encoder_pref in available:
        return encoder_pref
    logger.warning(
        "Requested AAC encoder %r is not available; falling back to auto", encoder_pref
    )
    return _best_aac_encoder(ffmpeg_path)


def _aac_quality_args(
    quality: str,
    options: Optional[TranscodeOptions] = None,
) -> list[str]:
    """Build encoder-specific ffmpeg flags from user settings.

    ``quality`` is treated as a boolean: ``"spoken"`` selects the
    spoken-word bitrate; anything else selects the music bitrate.
    """
    options = options or TranscodeOptions()
    encoder = _resolve_encoder(options.aac_encoder, options.ffmpeg_path)
    is_spoken = quality == "spoken"
    bitrate = (
        options.aac_spoken_bitrate if is_spoken else options.aac_music_bitrate
    )
    # Spoken-word transcodes always use CBR for compatibility.
    mode = "cbr" if is_spoken else options.aac_mode
    vbr_level = options.aac_vbr_level

    if encoder == "libfdk_aac":
        if mode == "vbr":
            return ["-vbr", str(vbr_level), "-profile:a", "aac_low"]
        return ["-b:a", f"{bitrate}k", "-profile:a", "aac_low"]

    if encoder == "aac_at":
        _mode_map = {"cbr": "cbr", "cvbr": "cvbr", "abr": "abr", "vbr": "vbr"}
        at_mode = _mode_map.get(mode, "cvbr")
        return ["-aac_at_mode", at_mode, "-b:a", f"{bitrate}k"]

    # aac (native fallback): CBR only, disable PNS for better old-iPod compat
    return ["-b:a", f"{bitrate}k", "-aac_pns", "0"]


# ═══════════════════════════════════════════════════════════════════════════
# FFmpeg command builders
# ═══════════════════════════════════════════════════════════════════════════

def _target_sample_rate(source_rate: int, normalize: bool) -> Optional[int]:
    """Return the ``-ar`` value to pass to ffmpeg, or ``None`` to omit the flag.

    Rules:
    - If source rate is unknown (0) → cap to IPOD_MAX_SAMPLE_RATE.
    - If source rate exceeds iPod limit → cap to IPOD_MAX_SAMPLE_RATE.
    - If ``normalize`` is True → always output 44 100 Hz (CD rate).
    - Otherwise → preserve source rate (no -ar flag).

    The iPod hardware accepts 44 100 Hz and 48 000 Hz equally well; we avoid
    upsampling 44.1 kHz sources to 48 000 Hz because that shifts the
    sample_count stored in iTunesDB and causes early track termination.
    """
    if source_rate == 0 or source_rate > IPOD_MAX_SAMPLE_RATE:
        return IPOD_MAX_SAMPLE_RATE
    if normalize:
        return 44_100
    return None   # preserve source rate


def _cmd_alac(ffmpeg: str, src: str, dst: str, normalize_sr: bool = False) -> list[str]:
    props = probe_audio(src)
    target_sr = _target_sample_rate(props.sample_rate, normalize_sr)
    ar_args = ["-ar", str(target_sr)] if target_sr is not None else []
    return [
        ffmpeg, "-i", src,
        "-vn",
        "-acodec", "alac",
        *ar_args,
        "-sample_fmt", "s16p",
        "-ac", str(IPOD_MAX_CHANNELS),
        "-movflags", "+faststart",
        "-y", dst,
    ]


def _cmd_aac(
    ffmpeg: str, src: str, dst: str, quality: str,
    normalize_sr: bool = False,
    mono: bool = False,
    options: Optional[TranscodeOptions] = None,
) -> list[str]:
    props = probe_audio(src)
    target_sr = _target_sample_rate(props.sample_rate, normalize_sr)
    ar_args = ["-ar", str(target_sr)] if target_sr is not None else []
    # Mono downmix: spoken-word at 64 kbps sounds better in mono (~50% smaller)
    channels = 1 if mono else IPOD_MAX_CHANNELS
    options = options or TranscodeOptions()
    _enc = _resolve_encoder(options.aac_encoder, options.ffmpeg_path)
    return [
        ffmpeg, "-i", src,
        "-vn",
        "-acodec", _enc,
        *ar_args,
        "-ac", str(channels),
        *_aac_quality_args(quality, options),
        "-movflags", "+faststart",
        "-y", dst,
    ]


def _cmd_video(
    ffmpeg: str, src: str, dst: str,
    quality: str, crf: int, preset: str,
    options: Optional[TranscodeOptions] = None,
) -> list[str]:
    options = options or TranscodeOptions()
    max_w, max_h, max_fps, max_bitrate, h264_level = _get_video_caps()

    # Rotate portrait videos 90° CW when the target is landscape —
    # a tiny centred strip wastes most of the iPod's fixed-landscape screen.
    # passthrough=landscape means "leave landscape videos alone, only rotate
    # portrait ones".  Applied before scaling so dimensions are correct.
    vf_parts: list[str] = []
    if max_w > max_h:
        vf_parts.append("transpose=1:passthrough=landscape")
    vf_parts.append(
        f"scale={max_w}:{max_h}"
        ":force_original_aspect_ratio=decrease,"
        "scale='trunc(iw/2)*2':'trunc(ih/2)*2'"
    )
    # Cap frame rate to device maximum (handles 60fps sources for Nano 3G/4G,
    # and prevents excessive bitrate on high-fps content)
    vf_parts.append(f"fps=fps={max_fps}")

    # Hard bitrate ceiling — enforced on devices with Level 1.3 decoders
    # (Nano 3G/4G: 768 kbps).  Uses a 2× buffer so the encoder has headroom.
    bitrate_args: list[str] = []
    if max_bitrate > 0:
        bitrate_args = ["-maxrate", f"{max_bitrate}k", "-bufsize", f"{max_bitrate * 2}k"]

    return [
        ffmpeg, "-i", src,
        "-map", "0:v:0", "-map", "0:a:0",
        "-vcodec", "libx264",
        "-profile:v", "baseline", "-level", h264_level,
        "-pix_fmt", "yuv420p",
        "-tag:v", "avc1",
        "-vf", ",".join(vf_parts),
        "-crf", str(crf), "-preset", preset,
        *bitrate_args,
        "-acodec", _resolve_encoder(options.aac_encoder, options.ffmpeg_path),
        "-ac", str(IPOD_MAX_CHANNELS),
        "-ar", str(IPOD_MAX_SAMPLE_RATE),
        "-b:a", "160k",
        "-movflags", "+faststart",
        "-f", "ipod",
        "-y", dst,
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Transcode execution
# ═══════════════════════════════════════════════════════════════════════════

def transcode(
    source_path: str | Path,
    output_dir: str | Path,
    output_filename: Optional[str] = None,
    ffmpeg_path: Optional[str] = None,
    aac_quality: str = "normal",
    progress_callback: Optional[Callable[[float], None]] = None,
    *,
    prefer_lossy: Optional[bool] = None,
    options: Optional[TranscodeOptions] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> TranscodeResult:
    """Transcode (or copy) *source_path* into *output_dir*.

    All iPod hardware limits are enforced automatically.
    Set *prefer_lossy* to force lossless sources to AAC.
    """
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    options = options or TranscodeOptions()

    if not source_path.exists():
        return TranscodeResult(
            success=False, source_path=source_path, output_path=None,
            target_format=TranscodeTarget.COPY, was_transcoded=False,
            error_message=f"Source file not found: {source_path}",
        )

    plan = resolve_transcode_plan(
        source_path,
        aac_quality=aac_quality,
        prefer_lossy=prefer_lossy,
        options=options,
    )
    target = plan.target
    base_name = output_filename or source_path.stem

    # ── COPY ────────────────────────────────────────────────────────────
    if target == TranscodeTarget.COPY:
        out = output_dir / (base_name + source_path.suffix)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, out)
            return TranscodeResult(
                success=True, source_path=source_path, output_path=out,
                target_format=target, was_transcoded=False,
            )
        except Exception as e:
            return TranscodeResult(
                success=False, source_path=source_path, output_path=None,
                target_format=target, was_transcoded=False,
                error_message=str(e),
            )

    # ── Transcode ───────────────────────────────────────────────────────
    ffmpeg = ffmpeg_path or find_ffmpeg(options.ffmpeg_path)
    if not ffmpeg:
        return TranscodeResult(
            success=False, source_path=source_path, output_path=None,
            target_format=target, was_transcoded=False,
            error_message="ffmpeg not found",
        )

    ext = plan.output_extension
    out = output_dir / (base_name + ext)
    src, dst = str(source_path), str(out)

    # Smart quality: override aac_quality for podcast/audiobook content types
    effective_quality = plan.effective_quality
    if plan.smart_quality_by_type and target == TranscodeTarget.AAC and plan.is_spoken:
        logger.debug(
            "smart_quality_by_type: spoken-word tags detected for %s",
            source_path.name,
        )

    # Mono downmix: only for spoken-word AAC transcodes
    use_mono = plan.mono_for_spoken and plan.is_spoken and target == TranscodeTarget.AAC

    if target == TranscodeTarget.ALAC:
        cmd = _cmd_alac(ffmpeg, src, dst, normalize_sr=plan.normalize_sample_rate)
    elif target == TranscodeTarget.AAC:
        cmd = _cmd_aac(ffmpeg, src, dst, effective_quality,
                       normalize_sr=plan.normalize_sample_rate, mono=use_mono,
                       options=options)
    else:
        cmd = _cmd_video(
            ffmpeg,
            src,
            dst,
            effective_quality,
            plan.video_crf,
            plan.video_preset,
            options=options,
        )

    return _run_transcode(cmd, source_path, out, target, progress_callback,
                          is_cancelled=is_cancelled)


def _run_transcode(
    cmd: list[str],
    source_path: Path,
    output_path: Path,
    target: TranscodeTarget,
    progress_callback: Optional[Callable[[float], None]],
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> TranscodeResult:
    """Run an ffmpeg command and return a TranscodeResult."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        timeout = 7200 if target == TranscodeTarget.VIDEO_H264 else 600

        if progress_callback and target == TranscodeTarget.VIDEO_H264:
            dur = _probe_duration_us(source_path)
            returncode, stderr = _run_ffmpeg_with_progress(
                cmd, dur, progress_callback, timeout,
                is_cancelled=is_cancelled,
            )
            progress_callback(1.0)
        else:
            # Audio transcodes: run via Popen so we can kill on cancel.
            # stdout is unused; stderr must be drained in a thread to
            # prevent a deadlock on Windows where small pipe buffers
            # (4 KB) fill up and block ffmpeg when multiple workers run
            # in parallel.
            import threading as _threading

            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                **_SP_KWARGS,
            )
            stderr_chunks: list[bytes] = []

            def _drain_stderr() -> None:
                pipe = proc.stderr
                if pipe is None:
                    return
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        stderr_chunks.append(chunk.encode("utf-8", errors="replace"))
                    else:
                        stderr_chunks.append(chunk)

            drain_t = _threading.Thread(target=_drain_stderr, daemon=True)
            drain_t.start()

            # Poll so we can check cancellation every 0.5s
            while proc.poll() is None:
                if is_cancelled and is_cancelled():
                    proc.kill()
                    proc.wait(timeout=5)
                    drain_t.join(timeout=5)
                    return TranscodeResult(
                        success=False, source_path=source_path,
                        output_path=None, target_format=target,
                        was_transcoded=True, error_message="Cancelled",
                    )
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass

            drain_t.join(timeout=10)
            returncode = proc.returncode
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        if returncode != 0:
            return TranscodeResult(
                success=False, source_path=source_path, output_path=None,
                target_format=target, was_transcoded=True,
                error_message=f"ffmpeg failed: {stderr[:500]}",
            )
        if not output_path.exists():
            return TranscodeResult(
                success=False, source_path=source_path, output_path=None,
                target_format=target, was_transcoded=True,
                error_message="Output file not created",
            )
        logger.info("Transcoded %s → %s", source_path.name, output_path.name)
        return TranscodeResult(
            success=True, source_path=source_path, output_path=output_path,
            target_format=target, was_transcoded=True,
        )
    except subprocess.TimeoutExpired:
        return TranscodeResult(
            success=False, source_path=source_path, output_path=None,
            target_format=target, was_transcoded=True,
            error_message="Transcoding timed out",
        )
    except Exception as e:
        return TranscodeResult(
            success=False, source_path=source_path, output_path=None,
            target_format=target, was_transcoded=True,
            error_message=str(e),
        )


def _run_ffmpeg_with_progress(
    cmd: list[str],
    duration_us: int,
    progress_callback: Callable[[float], None],
    timeout: int,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> tuple[int, str]:
    """Run ffmpeg with ``-progress pipe:1`` and stream progress."""
    import threading

    full_cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        **_SP_KWARGS,
    )

    stderr_chunks: list[str] = []

    def _drain():
        assert proc.stderr is not None
        for chunk in proc.stderr:
            stderr_chunks.append(chunk)

    t = threading.Thread(target=_drain, daemon=True)
    t.start()

    last_report = 0.0
    try:
        deadline = time.monotonic() + timeout
        assert proc.stdout is not None
        for line in proc.stdout:
            if is_cancelled and is_cancelled():
                proc.kill()
                t.join(timeout=5)
                return -1, "Cancelled"
            if time.monotonic() > deadline:
                proc.kill()
                return -1, "Transcoding timed out"
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    current = int(line.split("=", 1)[1])
                except (ValueError, IndexError):
                    continue
                frac = min(current / duration_us, 1.0) if duration_us > 0 else 0.0
                now = time.monotonic()
                if now - last_report >= 0.25 or frac >= 1.0:
                    progress_callback(frac)
                    last_report = now
        t.join(timeout=10)
        proc.wait(timeout=30)
    except Exception as e:
        proc.kill()
        t.join(timeout=5)
        return -1, str(e)

    return proc.returncode, "".join(stderr_chunks)


# ═══════════════════════════════════════════════════════════════════════════
# Metadata copy
# ═══════════════════════════════════════════════════════════════════════════

_MP4_COPY_KEYS = [
    "\xa9wrt",                                      # Composer
    "pcst", "catg", "purl", "egid", "stik",         # Podcast
    "cpil", "rtng", "tmpo", "desc", "ldes",         # Misc
    "tvsh", "tvsn", "tves", "tven", "tvnn",         # TV show
    "soar", "sonm", "soal", "soaa", "soco", "sosn",  # Sort
]


def copy_metadata(source_path: str | Path, dest_path: str | Path) -> bool:
    """Copy metadata tags from *source_path* to *dest_path*.

    Phase 1: common tags via mutagen's easy interface.
    Phase 2: format-specific atoms (podcast/TV/sort) via raw tags.
    """
    try:
        from mutagen._file import File as MutagenFile

        # Phase 1 — common tags
        src = MutagenFile(source_path, easy=True)
        dst = MutagenFile(dest_path, easy=True)
        if src is None or dst is None:
            return False
        for tag in (
            "title", "artist", "album", "albumartist", "genre",
            "date", "tracknumber", "discnumber", "composer",
        ):
            if tag in src:
                try:
                    dst[tag] = src[tag]
                except (KeyError, ValueError):
                    pass
        dst.save()

        # Phase 2 — raw atoms / frames
        src_raw = MutagenFile(source_path)
        dst_raw = MutagenFile(dest_path)
        if src_raw is None or dst_raw is None:
            return True
        src_tags, dst_tags = src_raw.tags, dst_raw.tags
        if src_tags is None or dst_tags is None:
            return True

        from mutagen.mp4 import MP4Tags
        if isinstance(src_tags, MP4Tags) and isinstance(dst_tags, MP4Tags):
            for key in _MP4_COPY_KEYS:
                if key in src_tags:
                    dst_tags[key] = src_tags[key]
            dst_raw.save()

        from mutagen.id3 import ID3
        if isinstance(src_tags, ID3) and isinstance(dst_tags, ID3):
            for frame_id in ("PCST", "TCAT", "WFED"):
                if frame_id in src_tags:
                    dst_tags.add(src_tags[frame_id])
            for frame in src_tags.getall("TXXX"):
                if getattr(frame, "desc", "") in ("PODCAST", "CATEGORY", "PODCAST_URL"):
                    dst_tags.add(frame)
            dst_raw.save()

        return True
    except Exception as e:
        logger.warning("Could not copy metadata: %s", e)
        return False
