"""Preview images for the dashboard (CLAUDE.md §15).

PROVEN  -> the recovered PNG bytes themselves, unchanged.
PARTIAL -> a picture of ONLY the verified rows: the verified IDAT data is
           inflated, every row that decoded completely is drawn, and every row
           after it is a diagonal hatch. The hatch is a placeholder that cannot
           be mistaken for image content — no missing bytes are guessed or filled.
REJECTED -> no preview.
"""
import io
import zlib
from typing import Optional, Tuple

import numpy as np
from PIL import Image as PILImage

from core.validate_png import _row_bytes, validate_png

HATCH_DARK, HATCH_LIGHT = 40, 200     # stripe colours for rows that were not recovered
HATCH_PERIOD, HATCH_WIDTH = 10, 3     # a light stripe 3 px wide every 10 px
CHANNELS = {0: 1, 2: 3, 6: 4}              # greyscale, RGB, RGBA (8-bit only)
PIL_MODE = {0: "L", 2: "RGB", 6: "RGBA"}


def _hatch(height: int, width: int, channels: int) -> np.ndarray:
    """Diagonal stripes, used only as a background for rows that were not recovered."""
    y, x = np.indices((height, width))
    stripe = (x + y) % HATCH_PERIOD < HATCH_WIDTH
    plane = np.where(stripe, HATCH_LIGHT, HATCH_DARK).astype(np.uint8)
    pixels = np.repeat(plane[:, :, None], channels, axis=2)
    if channels == 4:
        pixels[:, :, 3] = 255                          # opaque, so the hatch stays visible
    return pixels.reshape(height, width * channels)


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
    pixels = _hatch(height, width, channels)
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


def preview_png(state: str, artifact_bytes: bytes) -> Optional[Tuple[bytes, dict]]:
    """PNG bytes to show for an artifact, plus facts about it. None = no preview."""
    if state == "PROVEN":
        with PILImage.open(io.BytesIO(artifact_bytes)) as im:
            height = im.height
        return artifact_bytes, {"rows": height, "height": height,
                                "note": "recovered file, unchanged"}
    if state != "PARTIAL":
        return None
    rendered = partial_rows(artifact_bytes)
    if rendered is None:
        return None
    image, rows, height = rendered
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), {"rows": rows, "height": height,
                               "note": f"{rows} of {height} rows verified; hatched area not recovered"}
