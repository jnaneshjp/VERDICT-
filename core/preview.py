"""Preview images for the dashboard (CLAUDE.md §15).

PROVEN  -> the recovered PNG bytes themselves, unchanged.
PARTIAL -> a picture of ONLY the verified rows: the verified IDAT data is
           inflated, every row that decoded completely is drawn, and every row
           after it is flat grey. Grey is a placeholder, not recovered data —
           no missing bytes are guessed or filled.
REJECTED -> no preview.
"""
import io
import zlib
from typing import Optional, Tuple

import numpy as np
from PIL import Image as PILImage

from core.validate_png import _row_bytes, validate_png

GREY = 128
CHANNELS = {0: 1, 2: 3, 6: 4}              # greyscale, RGB, RGBA (8-bit only)
PIL_MODE = {0: "L", 2: "RGB", 6: "RGBA"}


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter_row(filter_type: int, row: bytearray, prev: bytearray, bpp: int) -> bytearray:
    """Undo one PNG scanline filter (PNG spec §9). Raises ValueError on an unknown type."""
    out = bytearray(row)
    for i in range(len(out)):
        a = out[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if filter_type == 0:
            pred = 0
        elif filter_type == 1:
            pred = a
        elif filter_type == 2:
            pred = b
        elif filter_type == 3:
            pred = (a + b) // 2
        elif filter_type == 4:
            pred = _paeth(a, b, c)
        else:
            raise ValueError(f"unknown PNG filter type {filter_type}")
        out[i] = (out[i] + pred) & 0xFF
    return out


def partial_rows(png_prefix: bytes) -> Optional[Tuple[PILImage.Image, int, int]]:
    """Render the verified rows of a PNG prefix. Returns (image, rows_drawn, height)."""
    result = validate_png(png_prefix)
    if result.width is None or result.bit_depth != 8 or result.color_type not in CHANNELS:
        return None
    width, height, channels = result.width, result.height, CHANNELS[result.color_type]
    idat = b"".join(png_prefix[c.offset + 8:c.offset + 8 + c.length]
                    for c in result.chunk_checks if c.ok and c.type == "IDAT")
    try:
        inflated = zlib.decompressobj().decompress(idat)
    except zlib.error:
        return None

    stride = _row_bytes(width, result.color_type, 8)
    pixels = np.full((height, width * channels), GREY, dtype=np.uint8)
    prev = bytearray(width * channels)
    rows = 0
    for y in range(min(len(inflated) // stride, height)):
        line = inflated[y * stride:(y + 1) * stride]
        try:
            prev = _unfilter_row(line[0], bytearray(line[1:]), prev, channels)
        except ValueError:
            break
        pixels[y] = np.frombuffer(bytes(prev), dtype=np.uint8)
        rows += 1
    shape = (height, width) if channels == 1 else (height, width, channels)
    return PILImage.fromarray(pixels.reshape(shape), PIL_MODE[result.color_type]), rows, height


def preview_png(state: str, artifact_bytes: bytes) -> Optional[Tuple[bytes, str]]:
    """PNG bytes to show for an artifact, plus a short note. None = no preview."""
    if state == "PROVEN":
        return artifact_bytes, "recovered file, unchanged"
    if state != "PARTIAL":
        return None
    rendered = partial_rows(artifact_bytes)
    if rendered is None:
        return None
    image, rows, height = rendered
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), f"{rows} of {height} rows verified; grey rows are not recovered"
