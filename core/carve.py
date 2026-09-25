"""P0 PNG signature carving (CLAUDE.md §3, §20 task 2).

Per CLAUDE.md §3: files start on block boundaries, so carving checks offset 0
of each block only. Reports the anchor location — no chunk parsing, no CRC
validation, no evidence claims.
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
    """PNG signature at offset 0 of each block (CLAUDE.md §3), ascending by block."""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    sig_len = len(PNG_SIGNATURE)
    total = len(raw)
    anchors: List[Anchor] = []
    for block_index in range(0, total, block_size):
        if block_index + sig_len > total:
            break                       # short trailing block cannot hold the signature
        if raw[block_index:block_index + sig_len] == PNG_SIGNATURE:
            anchors.append(Anchor(byte_offset=block_index,
                                  block_index=block_index // block_size))
    return anchors


def carve_png_anchors(image: Image) -> List[Anchor]:
    """Carve the PNG anchors of an already-ingested Image."""
    return find_png_anchors(image.raw, block_size=image.block_size)


# Deterministic classification (CLAUDE.md §9): signature -> type -> category. No ML.
CATEGORY_BY_TYPE = {"png": "image"}


def classify(signature: str) -> dict:
    """Map a carved signature to its file type and investigative category."""
    return {"type": signature, "category": CATEGORY_BY_TYPE[signature]}
