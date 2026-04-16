"""Format-aware ithmb encode/decode helpers.

This module maps Apple correlation IDs to pixel codecs so callers can encode
and decode artwork without assuming every format is RGB565 little-endian.
"""

from __future__ import annotations

from typing import Optional
import io

import numpy as np
from PIL import Image

from ipod_models import ITHMB_FORMAT_MAP


def _fmt(format_id: int):
    return ITHMB_FORMAT_MAP.get(format_id)


def format_pixel_format(format_id: int) -> str:
    fmt = _fmt(format_id)
    return (fmt.pixel_format if fmt is not None else "UNKNOWN")


def format_dimensions(format_id: int, fallback_w: int, fallback_h: int) -> tuple[int, int]:
    fmt = _fmt(format_id)
    if fmt is None:
        return fallback_w, fallback_h
    return int(fmt.width), int(fmt.height)


def default_stride_pixels(format_id: int, width: int) -> int:
    fmt = _fmt(format_id)
    if fmt is None:
        return width

    pf = fmt.pixel_format
    if pf in ("RGB565_LE", "RGB565_BE", "RGB565_BE_90", "RGB555_LE", "RGB555_BE"):
        return max(width, int(fmt.row_bytes // 2) if fmt.row_bytes else width)
    if pf.startswith("REC_RGB555"):
        return max(width, int(fmt.row_bytes // 2) if fmt.row_bytes else width)
    if pf == "UYVY":
        return max(width, int(fmt.row_bytes // 2) if fmt.row_bytes else width)

    return width


def expected_size_bytes(format_id: int, width: int, height: int, stride_pixels: Optional[int] = None) -> int:
    pf = format_pixel_format(format_id)
    stride = stride_pixels if stride_pixels is not None else default_stride_pixels(format_id, width)

    if pf in (
        "RGB565_LE",
        "RGB565_BE",
        "RGB565_BE_90",
        "RGB555_LE",
        "RGB555_BE",
        "UYVY",
    ) or pf.startswith("REC_RGB555"):
        return int(stride) * int(height) * 2
    if pf == "I420_LE":
        w = int(width) & ~1
        h = int(height) & ~1
        return (w * h * 3) // 2
    if pf == "JPEG":
        # Variable-size payload by design.
        return 0
    if pf == "UNKNOWN":
        return 0

    return int(stride) * int(height) * 2


def _rgb565_array_from_image(img: Image.Image) -> np.ndarray:
    arr = np.array(img.convert("RGB"), dtype=np.uint32)
    r = (arr[:, :, 0] >> 3) & 0x1F
    g = (arr[:, :, 1] >> 2) & 0x3F
    b = (arr[:, :, 2] >> 3) & 0x1F
    return ((r << 11) | (g << 5) | b).astype(np.uint16)


def _rgb565_to_rgb(arr16: np.ndarray) -> np.ndarray:
    r = ((arr16 >> 11) & 0x1F).astype(np.uint8)
    g = ((arr16 >> 5) & 0x3F).astype(np.uint8)
    b = (arr16 & 0x1F).astype(np.uint8)
    return np.stack(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)), axis=2)


def _rgb555_array_from_image(img: Image.Image) -> np.ndarray:
    arr = np.array(img.convert("RGB"), dtype=np.uint32)
    r = (arr[:, :, 0] >> 3) & 0x1F
    g = (arr[:, :, 1] >> 3) & 0x1F
    b = (arr[:, :, 2] >> 3) & 0x1F
    return ((r << 10) | (g << 5) | b).astype(np.uint16)


def _rgb555_to_rgb(arr16: np.ndarray) -> np.ndarray:
    r = ((arr16 >> 10) & 0x1F).astype(np.uint8)
    g = ((arr16 >> 5) & 0x1F).astype(np.uint8)
    b = (arr16 & 0x1F).astype(np.uint8)
    return np.stack(((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2)), axis=2)


def encode_image_for_format(
    source_img: Image.Image,
    format_id: int,
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
) -> dict:
    pf = format_pixel_format(format_id)
    w, h = format_dimensions(
        format_id,
        int(target_width or source_img.width),
        int(target_height or source_img.height),
    )

    base = source_img.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)

    if pf == "RGB565_BE_90":
        rotated = base.transpose(Image.Transpose.ROTATE_270)
        arr16 = _rgb565_array_from_image(rotated)
        raw = arr16.astype(">u2").tobytes()
        return {
            "data": raw,
            "width": w,
            "height": h,
            "size": len(raw),
            "stride_pixels": default_stride_pixels(format_id, w),
            "pixel_format": pf,
        }

    if pf == "RGB565_BE":
        arr16 = _rgb565_array_from_image(base)
        raw = arr16.astype(">u2").tobytes()
        return {
            "data": raw,
            "width": w,
            "height": h,
            "size": len(raw),
            "stride_pixels": default_stride_pixels(format_id, w),
            "pixel_format": pf,
        }

    if pf == "RGB555_BE":
        arr16 = _rgb555_array_from_image(base)
        raw = arr16.astype(">u2").tobytes()
        return {
            "data": raw,
            "width": w,
            "height": h,
            "size": len(raw),
            "stride_pixels": default_stride_pixels(format_id, w),
            "pixel_format": pf,
        }

    if pf in ("RGB555_LE", "REC_RGB555_LE"):
        arr16 = _rgb555_array_from_image(base)
        raw = arr16.astype("<u2").tobytes()
        return {
            "data": raw,
            "width": w,
            "height": h,
            "size": len(raw),
            "stride_pixels": default_stride_pixels(format_id, w),
            "pixel_format": pf,
        }

    if pf == "JPEG":
        out = io.BytesIO()
        # Use fixed quality to keep writes deterministic enough for debugging.
        base.save(out, format="JPEG", quality=92, optimize=False)
        raw = out.getvalue()
        return {
            "data": raw,
            "width": w,
            "height": h,
            "size": len(raw),
            "stride_pixels": default_stride_pixels(format_id, w),
            "pixel_format": pf,
        }

    if pf == "UYVY":
        if w % 2 != 0:
            w -= 1
            base = base.resize((w, h), Image.Resampling.LANCZOS)

        arr = np.array(base, dtype=np.float32)
        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]
        y = np.clip(0.257 * r + 0.504 * g + 0.098 * b + 16, 0, 255).astype(np.uint8)
        u = np.clip(-0.148 * r - 0.291 * g + 0.439 * b + 128, 0, 255)
        v = np.clip(0.439 * r - 0.368 * g - 0.071 * b + 128, 0, 255)
        u2 = ((u[:, 0::2] + u[:, 1::2]) * 0.5).astype(np.uint8)
        v2 = ((v[:, 0::2] + v[:, 1::2]) * 0.5).astype(np.uint8)
        packed = np.empty((h, w * 2), dtype=np.uint8)
        packed[:, 0::4] = u2
        packed[:, 1::4] = y[:, 0::2]
        packed[:, 2::4] = v2
        packed[:, 3::4] = y[:, 1::2]
        raw = packed.tobytes()
        return {
            "data": raw,
            "width": w,
            "height": h,
            "size": len(raw),
            "stride_pixels": default_stride_pixels(format_id, w),
            "pixel_format": pf,
        }

    if pf == "I420_LE":
        w_even = w & ~1
        h_even = h & ~1
        if w_even != w or h_even != h:
            w, h = w_even, h_even
            base = base.resize((w, h), Image.Resampling.LANCZOS)

        arr = np.array(base, dtype=np.float32)
        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]
        y = np.clip(0.257 * r + 0.504 * g + 0.098 * b + 16, 0, 255).astype(np.uint8)
        u = np.clip(-0.148 * r - 0.291 * g + 0.439 * b + 128, 0, 255)
        v = np.clip(0.439 * r - 0.368 * g - 0.071 * b + 128, 0, 255)
        u420 = ((u[0::2, 0::2] + u[0::2, 1::2] + u[1::2, 0::2] + u[1::2, 1::2]) * 0.25).astype(np.uint8)
        v420 = ((v[0::2, 0::2] + v[0::2, 1::2] + v[1::2, 0::2] + v[1::2, 1::2]) * 0.25).astype(np.uint8)
        raw = y.tobytes() + u420.tobytes() + v420.tobytes()
        return {
            "data": raw,
            "width": w,
            "height": h,
            "size": len(raw),
            "stride_pixels": default_stride_pixels(format_id, w),
            "pixel_format": pf,
        }

    if pf == "UNKNOWN":
        raise ValueError(f"Unsupported unknown pixel format for format_id={format_id}")

    # Default and common path: RGB565 little-endian.
    arr16 = _rgb565_array_from_image(base)
    raw = arr16.astype("<u2").tobytes()
    return {
        "data": raw,
        "width": w,
        "height": h,
        "size": len(raw),
        "stride_pixels": default_stride_pixels(format_id, w),
        "pixel_format": "RGB565_LE",
    }


def decode_pixels_for_format(
    format_id: int,
    pixel_bytes: bytes,
    width: int,
    height: int,
    hpad: int = 0,
    vpad: int = 0,
) -> Optional[Image.Image]:
    pf = format_pixel_format(format_id)
    width = max(1, int(width))
    height = max(1, int(height))
    hpad = max(0, int(hpad))
    vpad = max(0, int(vpad))

    if pf in ("RGB565_LE", "RGB565_BE", "RGB565_BE_90"):
        stored_w = width
        stored_h = height
        px_count = len(pixel_bytes) // 2
        if stored_w * stored_h != px_count:
            if stored_h <= 0 or px_count % stored_h != 0:
                return None
            stored_w = px_count // stored_h

        dtype = "<u2" if pf == "RGB565_LE" else ">u2"
        arr = np.frombuffer(pixel_bytes, dtype=dtype)
        if arr.size != stored_w * stored_h:
            return None
        arr = arr.reshape((stored_h, stored_w))

        rgb = _rgb565_to_rgb(arr)
        if pf == "RGB565_BE_90":
            rgb = np.rot90(rgb, k=1)

        visible_w = max(1, min(rgb.shape[1], width - hpad if hpad < width else width))
        visible_h = max(1, min(rgb.shape[0], height - vpad if vpad < height else height))
        rgb = rgb[:visible_h, :visible_w, :]
        return Image.fromarray(rgb, mode="RGB")

    if pf in ("RGB555_LE", "RGB555_BE", "REC_RGB555_LE"):
        stored_w = width
        stored_h = height
        px_count = len(pixel_bytes) // 2
        if stored_w * stored_h != px_count:
            if stored_h <= 0 or px_count % stored_h != 0:
                return None
            stored_w = px_count // stored_h

        dtype = "<u2" if pf != "RGB555_BE" else ">u2"
        arr = np.frombuffer(pixel_bytes, dtype=dtype)
        if arr.size != stored_w * stored_h:
            return None
        arr = arr.reshape((stored_h, stored_w))

        rgb = _rgb555_to_rgb(arr)
        visible_w = max(1, min(rgb.shape[1], width - hpad if hpad < width else width))
        visible_h = max(1, min(rgb.shape[0], height - vpad if vpad < height else height))
        rgb = rgb[:visible_h, :visible_w, :]
        return Image.fromarray(rgb, mode="RGB")

    if pf == "UYVY":
        width &= ~1
        if len(pixel_bytes) < width * height * 2:
            return None
        p = np.frombuffer(pixel_bytes[: width * height * 2], dtype=np.uint8).reshape((height, width * 2))
        u = p[:, 0::4].astype(np.float32)
        y0 = p[:, 1::4].astype(np.float32)
        v = p[:, 2::4].astype(np.float32)
        y1 = p[:, 3::4].astype(np.float32)

        y = np.empty((height, width), dtype=np.float32)
        y[:, 0::2] = y0
        y[:, 1::2] = y1
        uu = np.repeat(u, 2, axis=1)
        vv = np.repeat(v, 2, axis=1)

        c = y - 16.0
        d = uu - 128.0
        e = vv - 128.0
        r = np.clip((298.082 * c + 408.583 * e) / 256.0, 0, 255).astype(np.uint8)
        g = np.clip((298.082 * c - 100.291 * d - 208.120 * e) / 256.0, 0, 255).astype(np.uint8)
        b = np.clip((298.082 * c + 516.412 * d) / 256.0, 0, 255).astype(np.uint8)
        rgb = np.stack((r, g, b), axis=2)
        return Image.fromarray(rgb, mode="RGB")

    if pf == "I420_LE":
        width &= ~1
        height &= ~1
        y_size = width * height
        uv_size = (width // 2) * (height // 2)
        if len(pixel_bytes) < y_size + uv_size + uv_size:
            return None
        y = np.frombuffer(pixel_bytes[:y_size], dtype=np.uint8).reshape((height, width)).astype(np.float32)
        u = np.frombuffer(pixel_bytes[y_size:y_size + uv_size], dtype=np.uint8).reshape((height // 2, width // 2)).astype(np.float32)
        v = np.frombuffer(pixel_bytes[y_size + uv_size:y_size + uv_size + uv_size], dtype=np.uint8).reshape((height // 2, width // 2)).astype(np.float32)
        uu = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1)
        vv = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1)

        c = y - 16.0
        d = uu - 128.0
        e = vv - 128.0
        r = np.clip((298.082 * c + 408.583 * e) / 256.0, 0, 255).astype(np.uint8)
        g = np.clip((298.082 * c - 100.291 * d - 208.120 * e) / 256.0, 0, 255).astype(np.uint8)
        b = np.clip((298.082 * c + 516.412 * d) / 256.0, 0, 255).astype(np.uint8)
        rgb = np.stack((r, g, b), axis=2)
        return Image.fromarray(rgb, mode="RGB")

    if pf == "JPEG":
        try:
            return Image.open(io.BytesIO(pixel_bytes)).convert("RGB")
        except Exception:
            return None

    return None
