"""Ingest a raw storage image into blocks (CLAUDE.md §3, §20 task 2).

No format parsing here — this module only builds the per-block metadata later
stages need: SHA-256, entropy, printable ratio, and the SHA-256 dedup map.
Byte content is preserved exactly, including a final partial block.

Block bytes are NOT copied onto each Block; consumers slice from Image.raw
(directly or via Image.block_data) to avoid holding the image twice in memory.
"""
import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from core.config import BLOCK_SIZE

_PRINTABLE_MASK = np.zeros(256, dtype=bool)
_PRINTABLE_MASK[0x20:0x7F] = True         # visible ASCII including space
for _b in (0x09, 0x0A, 0x0D):             # tab, LF, CR
    _PRINTABLE_MASK[_b] = True


@dataclass
class Block:
    """Metadata for one block-sized slice (final block may be shorter)."""
    index: int
    offset: int
    length: int
    sha256: str
    entropy: float
    printable_ratio: float


@dataclass
class Image:
    """A parsed storage image and its per-block metadata."""
    path: Optional[str]
    size: int
    block_size: int
    block_count: int
    sha256: str
    raw: bytes
    blocks: List[Block]
    dedup: Dict[str, List[int]] = field(default_factory=dict)

    def duplicates(self) -> Dict[str, List[int]]:
        """SHA-256 -> block indices, restricted to hashes shared by 2+ blocks."""
        return {sha: idx for sha, idx in self.dedup.items() if len(idx) > 1}

    def block_data(self, block: "Block") -> bytes:
        """Bytes of `block`, sliced from the retained raw image."""
        return self.raw[block.offset:block.offset + block.length]


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256).astype(np.float64)
    probs = counts[counts > 0] / counts.sum()
    return float(-np.sum(probs * np.log2(probs)))


def _printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    arr = np.frombuffer(data, dtype=np.uint8)
    return float(_PRINTABLE_MASK[arr].sum()) / arr.size


def ingest_bytes(raw: bytes, path: Optional[str] = None, block_size: int = BLOCK_SIZE) -> Image:
    """Slice `raw` into `block_size`-byte blocks and compute per-block metadata."""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    size = len(raw)
    block_count = math.ceil(size / block_size) if size else 0
    blocks: List[Block] = []
    dedup: Dict[str, List[int]] = defaultdict(list)
    for index in range(block_count):
        offset = index * block_size
        chunk = raw[offset:offset + block_size]      # temporary; not stored on Block
        sha = hashlib.sha256(chunk).hexdigest()
        blocks.append(Block(
            index=index,
            offset=offset,
            length=len(chunk),
            sha256=sha,
            entropy=_entropy(chunk),
            printable_ratio=_printable_ratio(chunk),
        ))
        dedup[sha].append(index)
    return Image(
        path=path,
        size=size,
        block_size=block_size,
        block_count=block_count,
        sha256=hashlib.sha256(raw).hexdigest(),
        raw=raw,
        blocks=blocks,
        dedup=dict(dedup),
    )


def ingest_image(path: str, block_size: int = BLOCK_SIZE) -> Image:
    """Read the image at `path` and return the parsed Image."""
    with open(path, "rb") as handle:
        raw = handle.read()
    return ingest_bytes(raw, path=path, block_size=block_size)
