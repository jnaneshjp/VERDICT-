"""Task-4 verification harness for the reconstruction search (CLAUDE.md §20 task 4).

1. Runs reconstruct_png() on every carved PNG anchor of data/cases/demo01.img
   and prints the per-anchor outcome.
2. Runs a self-contained backtracking fixture that guarantees a real backtrack:
   the closest candidate to the anchor is a distractor block, so the correct
   next block is only reached after the distractor path is rejected.

Reconstruction uses only the ingested image; no ground-source metadata.
"""
import argparse
import hashlib
import os
import struct
import sys
import zlib

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.carve import Anchor, carve_png_anchors  # noqa: E402
from core.config import BLOCK_SIZE, BUDGET  # noqa: E402
from core.ingest import ingest_bytes, ingest_image  # noqa: E402
from core.search import ReconStatus, reconstruct_png  # noqa: E402


# ---------------------------------------------------------------- tiny PNG writer

def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _make_png(width: int, height: int, seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + pixels[row].tobytes() for row in range(height))
    stream = zlib.compress(raw, 9)
    out = bytearray(b"\x89PNG\r\n\x1a\n") + _chunk(b"IHDR", ihdr)
    for i in range(0, len(stream), 2048):
        out += _chunk(b"IDAT", stream[i:i + 2048])
    out += _chunk(b"IEND", b"")
    return bytes(out)


def _build_multiblock_png(seed: int = 1) -> bytes:
    """Random-pixel PNG that is guaranteed to exceed one 4096-byte block."""
    for size in (40, 55, 70, 90, 120):
        png = _make_png(size, size, seed)
        if len(png) > BLOCK_SIZE:
            return png
    raise RuntimeError("failed to synthesise a multi-block test PNG")


# ---------------------------------------------------------------- fixture

def run_backtracking_fixture() -> bool:
    print("\n[backtracking fixture]")
    png = _build_multiblock_png(seed=1)
    png_sha = hashlib.sha256(png).hexdigest()
    png_blocks = -(-len(png) // BLOCK_SIZE)                # ceil
    print(f"synthetic png: {len(png)} bytes, needs {png_blocks} block(s), sha256={png_sha}")
    assert png_blocks >= 2, "test PNG must span >= 2 blocks to exercise backtracking"

    distractor_rng = np.random.default_rng(999)
    distractor = distractor_rng.integers(0, 256, BLOCK_SIZE, dtype=np.uint8).tobytes()
    assert not distractor.startswith(b"\x89PNG\r\n\x1a\n"), "distractor must not carry a PNG signature"

    def slot(i: int) -> bytes:
        chunk = png[i * BLOCK_SIZE:(i + 1) * BLOCK_SIZE]
        return chunk + b"\x00" * (BLOCK_SIZE - len(chunk))

    layout = [slot(0)]
    for i in range(1, png_blocks):
        layout.append(distractor)                          # closer to previous PNG block
        layout.append(slot(i))                             # correct block, farther away
    raw = b"".join(layout)
    image = ingest_bytes(raw)
    anchor = Anchor(byte_offset=0, block_index=0)
    print(f"disk layout: {image.block_count} blocks; distractors interleaved between PNG blocks")

    result = reconstruct_png(image, anchor)

    print(f"result:")
    print(f"  status:           {result.status.value}")
    print(f"  reason:           {result.reason}")
    print(f"  path:             {result.path}")
    print(f"  validations:      {result.validation_count} / {BUDGET}")
    print(f"  max_depth:        {result.max_depth}")
    print(f"  backtracks:       {result.backtrack_count}")
    print(f"  branches_tried:   {result.branches_tried}")
    print(f"  reconstructed:    length={result.reconstructed_length}  "
          f"sha256={result.reconstructed_sha256}")

    ok = True
    if result.status is not ReconStatus.VERIFIED:
        print(f"  FAIL: expected VERIFIED, got {result.status.value}")
        ok = False
    if result.backtrack_count < 1:
        print(f"  FAIL: expected backtrack_count >= 1, got {result.backtrack_count}")
        ok = False
    if result.reconstructed_sha256 != png_sha:
        print(f"  FAIL: reconstructed sha does not match the synthetic PNG")
        ok = False
    if ok:
        print("  FIXTURE PASS")
    return ok


# ---------------------------------------------------------------- demo run

def run_demo(case: str) -> None:
    print(f"[demo reconstruction: {case}]")
    image_path = os.path.join(ROOT, "data", "cases", f"{case}.img")
    image = ingest_image(image_path)
    anchors = carve_png_anchors(image)
    print(f"image blocks={image.block_count}  png anchors={len(anchors)}")

    for anchor in anchors:
        result = reconstruct_png(image, anchor)
        print(f"\nanchor byte_offset={anchor.byte_offset}  block={anchor.block_index}")
        print(f"  status:           {result.status.value}")
        print(f"  reason:           {result.reason}")
        print(f"  path (verified):  {result.path}")
        if result.search_path != result.path:
            print(f"  search_path:      {result.search_path}")
        print(f"  verified_upto:    {result.verified_upto}")
        print(f"  validations:      {result.validation_count} / {BUDGET}")
        print(f"  max_depth:        {result.max_depth}")
        print(f"  backtracks:       {result.backtrack_count}")
        print(f"  branches_tried:   {result.branches_tried}")
        if result.status is ReconStatus.VERIFIED:
            print(f"  reconstructed:    length={result.reconstructed_length}  "
                  f"sha256={result.reconstructed_sha256}")
        elif result.final_validator is not None:
            fv = result.final_validator
            print(f"  final validator:  {fv.status.value}  "
                  f"{fv.reason_code}: {fv.reason}")


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Task-4 verification harness.")
    parser.add_argument("--case", default="demo01")
    parser.add_argument("--skip-demo", action="store_true")
    parser.add_argument("--skip-fixture", action="store_true")
    args = parser.parse_args()

    if not args.skip_demo:
        run_demo(args.case)
    fixture_ok = True
    if not args.skip_fixture:
        fixture_ok = run_backtracking_fixture()
    if not fixture_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
