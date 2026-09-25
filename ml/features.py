"""Candidate features for the ML ranker (CLAUDE.md §10).

Each feature compares the tail of the current path with one candidate block.
Features only help ORDER candidates; the PNG validator still decides what is
correct.

  1. signed offset delta      candidate - last block
  2. absolute offset delta
  3. candidate entropy
  4. entropy delta            last block entropy - candidate entropy
  5. histogram L1 distance    last 512 B of path vs first 512 B of candidate
  6. candidate printable ratio
  7. structural flag          does a plausible chunk header sit where the
                              current PNG chunk would end inside the candidate?
                              1 = yes, 0 = no, -1 = cannot tell from this block
"""
import struct
from typing import List

import numpy as np

from core.ingest import Image
from core.validate_png import MAX_CHUNK_LENGTH, Status, validate_png

FEATURE_NAMES = ["signed_delta", "abs_delta", "entropy", "entropy_delta",
                 "hist_l1", "printable", "chunk_header_flag"]
EDGE_BYTES = 512


def _histogram(data: bytes) -> np.ndarray:
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256)
    return counts / max(len(data), 1)


def _plausible_header(header: bytes) -> bool:
    """Sane length + four ASCII letters (the same rule the validator uses)."""
    length = struct.unpack(">I", header[:4])[0]
    return length <= MAX_CHUNK_LENGTH and all(
        0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A for b in header[4:8])


class StepContext:
    """Everything about the current path that every candidate is compared against."""

    def __init__(self, image: Image, path: List[int]):
        self.image = image
        self.last = path[-1]
        self.last_entropy = image.blocks[self.last].entropy
        data = b"".join(image.block_data(image.blocks[b]) for b in path)
        self.tail_hist = _histogram(data[-EDGE_BYTES:])
        # Bytes from the start of the unfinished PNG chunk to the end of the path.
        result = validate_png(data)
        self.open_chunk = data[result.bytes_examined:] if result.status is Status.INCOMPLETE else None

    def chunk_header_flag(self, cand: bytes) -> float:
        if self.open_chunk is None or len(self.open_chunk) + len(cand) < 8:
            return -1.0
        joined = self.open_chunk + cand
        length = struct.unpack(">I", joined[:4])[0]
        end = 12 + length                          # length + type + data + CRC
        if end + 8 > len(joined):
            return -1.0                            # chunk continues past this candidate
        return 1.0 if _plausible_header(joined[end:end + 8]) else 0.0


def candidate_features(ctx: StepContext, cand: int) -> List[float]:
    """The 7 §10 features for one candidate block."""
    block = ctx.image.blocks[cand]
    data = ctx.image.block_data(block)
    delta = cand - ctx.last
    return [
        float(delta),
        float(abs(delta)),
        block.entropy,
        ctx.last_entropy - block.entropy,
        float(np.abs(ctx.tail_hist - _histogram(data[:EDGE_BYTES])).sum()),
        block.printable_ratio,
        ctx.chunk_header_flag(data),
    ]


def features_for(image: Image, path: List[int], candidates: List[int]) -> np.ndarray:
    """Feature matrix, one row per candidate, in the candidates' order."""
    ctx = StepContext(image, path)
    return np.array([candidate_features(ctx, c) for c in candidates], dtype=np.float64)
