"""P0 PNG signature carving (CLAUDE.md §3, §20 task 2).

Scans the raw image for every occurrence of the 8-byte PNG signature at ANY
byte offset (not only block boundaries). Reports the location only — no chunk
parsing, no CRC validation, no evidence claims.
"""
from dataclasses import dataclass
from typing import List

from core.config import BLOCK_SIZE
from core.ingest import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class Anchor:
    """A candidate file start carved from the raw image."""
    byte_offset: int
    block_index: int
    signature: str = "png"


def find_png_anchors(raw: bytes, block_size: int = BLOCK_SIZE) -> List[Anchor]:
    """Every distinct byte offset in `raw` where the PNG signature begins, ascending."""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    anchors: List[Anchor] = []
    start = 0
    while True:
        offset = raw.find(PNG_SIGNATURE, start)
        if offset < 0:
            break
        anchors.append(Anchor(byte_offset=offset, block_index=offset // block_size))
        start = offset + 1
    return anchors


def carve_png_anchors(image: Image) -> List[Anchor]:
    """Carve the PNG anchors of an already-ingested Image."""
    return find_png_anchors(image.raw, block_size=image.block_size)
