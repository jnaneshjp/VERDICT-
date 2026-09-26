"""Candidate features for the DOCX / ZIP ranker (P1 prototype).

Features 1-6 are the PNG ranker's distance, entropy and byte-pattern features
(ml/features.py). The PNG chunk-header flag is replaced by two ZIP clues:

  7. zip_next_header   where the current entry's compressed data should end
                       inside the candidate, do the next 4 bytes read PK\\x03\\x04
                       (next entry) or PK\\x01\\x02 (central directory)?
                       1 = yes, 0 = no, -1 = cannot tell from this block
  8. inflate_continues does streaming inflate of the current entry continue
                       without a zlib error across the join into the candidate?
                       1 = yes, 0 = no, -1 = not inside a deflated entry

Features only ORDER candidates; the ZIP validator still decides correctness.
"""
import struct
import zlib
from typing import List

import numpy as np

from core.ingest import Image
from core.validate_png import Status
from core.validate_zip import CENTRAL_SIG, LOCAL_HEADER_SIZE, LOCAL_SIG, validate_zip
from ml.features import EDGE_BYTES, _histogram

FEATURE_NAMES = ["signed_delta", "abs_delta", "entropy", "entropy_delta",
                 "hist_l1", "printable", "zip_next_header", "inflate_continues"]


class ZipStepContext:
    """The current path, plus the open (unfinished) ZIP entry if there is one."""

    def __init__(self, image: Image, path: List[int]):
        self.image = image
        self.last = path[-1]
        self.last_entropy = image.blocks[self.last].entropy
        data = b"".join(image.block_data(image.blocks[b]) for b in path)
        self.tail_hist = _histogram(data[-EDGE_BYTES:])
        self.data_end = None        # offset (in path bytes) where the open entry's data ends
        self.inflater = None        # inflater that has consumed the open entry's data so far
        self.remaining = 0          # compressed bytes of the open entry still to come
        self.path_len = len(data)
        result = validate_zip(data)
        if result.status is not Status.INCOMPLETE:
            return
        start = result.verified_bytes              # the open entry's local header
        if data[start:start + 4] != LOCAL_SIG or len(data) - start < LOCAL_HEADER_SIZE:
            return
        method, _crc, csize, _size, name_len, extra_len = struct.unpack_from("<2xH4xIIIHH", data, start + 6)
        data_start = start + LOCAL_HEADER_SIZE + name_len + extra_len
        if data_start > len(data):
            return
        self.data_end = data_start + csize
        self.remaining = self.data_end - len(data)
        if method == 8 and self.remaining > 0:
            inflater = zlib.decompressobj(-15)
            try:
                inflater.decompress(data[data_start:])
                self.inflater = inflater
            except zlib.error:
                self.inflater = None

    def next_header_flag(self, cand: bytes) -> float:
        if self.data_end is None:
            return -1.0
        end = self.data_end - self.path_len         # where the entry ends inside the candidate
        if end < 0 or end + 4 > len(cand):
            return -1.0
        return 1.0 if cand[end:end + 4] in (LOCAL_SIG, CENTRAL_SIG) else 0.0

    def inflate_flag(self, cand: bytes) -> float:
        if self.inflater is None:
            return -1.0
        try:
            self.inflater.copy().decompress(cand[:self.remaining])
            return 1.0
        except zlib.error:
            return 0.0


def candidate_features(ctx: ZipStepContext, cand: int) -> List[float]:
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
        ctx.next_header_flag(data),
        ctx.inflate_flag(data),
    ]


def features_for(image: Image, path: List[int], candidates: List[int]) -> np.ndarray:
    ctx = ZipStepContext(image, path)
    return np.array([candidate_features(ctx, c) for c in candidates], dtype=np.float64)
