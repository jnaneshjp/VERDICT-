"""Task-2 verification harness for VERDICT (CLAUDE.md §20 task 2).

Runs ingest + carve on data/cases/<case>.img, prints image / block / anchor
metadata, checks ingest invariants, and exercises edge cases. Does not read
any file under data/truth/ — that stays behind evaluation/evaluate.py per §12.
"""
import argparse
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.config import BLOCK_SIZE  # noqa: E402
from core.carve import PNG_SIGNATURE, carve_png_anchors, find_png_anchors  # noqa: E402
from core.ingest import ingest_bytes, ingest_image  # noqa: E402


def check_ingest_invariants(image, raw):
    joined = b"".join(image.block_data(b) for b in image.blocks)
    assert joined == raw, "concatenated block bytes differ from original"
    assert image.raw == raw, "image.raw differs from original"
    assert sum(b.length for b in image.blocks) == image.size, "block length sum != image size"
    for block in image.blocks:
        assert block.offset == block.index * image.block_size, f"block {block.index}: wrong offset"
        slice_ = image.block_data(block)
        assert len(slice_) == block.length, f"block {block.index}: length field wrong"
        assert hashlib.sha256(slice_).hexdigest() == block.sha256, f"block {block.index}: sha256 wrong"


def run_edge_cases():
    print("\n[edge cases]")

    empty = ingest_bytes(b"")
    assert empty.size == 0 and empty.block_count == 0 and empty.blocks == []
    assert find_png_anchors(b"") == []
    print("  empty image                              block_count=0, no anchors                 ok")

    tiny = ingest_bytes(b"\x00" * 10)
    assert tiny.block_count == 1 and tiny.blocks[0].length == 10
    print("  image smaller than one block             block_count=1, length=10                  ok")

    partial_size = BLOCK_SIZE * 2 + 17
    partial = ingest_bytes(b"A" * partial_size)
    assert partial.block_count == 3
    assert partial.blocks[-1].length == 17
    assert sum(b.length for b in partial.blocks) == partial_size
    print(f"  non-divisible size ({partial_size} bytes) blocks=3, last length=17                 ok")

    at_start = bytearray(BLOCK_SIZE * 2)
    at_start[BLOCK_SIZE:BLOCK_SIZE + 8] = PNG_SIGNATURE
    anchors = find_png_anchors(bytes(at_start))
    assert [(a.byte_offset, a.block_index) for a in anchors] == [(BLOCK_SIZE, 1)]
    print(f"  signature at start of block              offset={BLOCK_SIZE}, block=1               ok")

    mid_block = bytearray(BLOCK_SIZE * 2)
    mid_block[100:108] = PNG_SIGNATURE
    assert find_png_anchors(bytes(mid_block)) == []
    print("  signature mid-block                      ignored (block-aligned scan, §3)          ok")

    crossing = bytearray(BLOCK_SIZE * 2)
    crossing[BLOCK_SIZE - 4:BLOCK_SIZE + 4] = PNG_SIGNATURE
    assert find_png_anchors(bytes(crossing)) == []
    print("  signature crossing boundary              ignored (block-aligned scan, §3)          ok")

    multi = bytearray(BLOCK_SIZE * 3)
    multi[0:8] = PNG_SIGNATURE
    multi[BLOCK_SIZE * 2:BLOCK_SIZE * 2 + 8] = PNG_SIGNATURE
    anchors = find_png_anchors(bytes(multi))
    assert [(a.byte_offset, a.block_index) for a in anchors] == [(0, 0), (BLOCK_SIZE * 2, 2)]
    print(f"  multiple signatures at block starts      offsets=[0, {BLOCK_SIZE * 2}], blocks=[0, 2]     ok")

    short_final = bytearray(BLOCK_SIZE + 4)
    short_final[BLOCK_SIZE:BLOCK_SIZE + 4] = b"\x89PNG"
    assert find_png_anchors(bytes(short_final)) == []
    print("  short final block (<8 bytes)             cannot hold signature, ignored            ok")


def main():
    parser = argparse.ArgumentParser(description="Task-2 verification harness.")
    parser.add_argument("--case", default="demo01")
    args = parser.parse_args()

    image_path = os.path.join(ROOT, "data", "cases", f"{args.case}.img")
    with open(image_path, "rb") as handle:
        raw = handle.read()
    image = ingest_image(image_path)
    check_ingest_invariants(image, raw)
    anchors = carve_png_anchors(image)

    print(f"case:              {args.case}")
    print(f"image path:        {os.path.relpath(image_path, ROOT)}")
    print(f"image size:        {image.size} bytes")
    print(f"image sha256:      {image.sha256}")
    print(f"block size:        {image.block_size}")
    print(f"block count:       {image.block_count}")
    print(f"blocks processed:  {len(image.blocks)}")

    duplicates = image.duplicates()
    extra_copies = sum(len(idx) - 1 for idx in duplicates.values())
    print(f"unique sha256s:    {len(image.dedup)}")
    print(f"duplicate groups:  {len(duplicates)}  (extra copies: {extra_copies})")
    for sha, idx in sorted(duplicates.items(), key=lambda kv: kv[1][0]):
        print(f"    {sha[:12]}... blocks {idx}")

    print(f"png anchors found: {len(anchors)}")
    for anchor in anchors:
        print(f"    offset {anchor.byte_offset:>10}  block {anchor.block_index}")

    print("ingest invariants: concat==raw ok  length-sum ok  per-block sha256 ok")

    run_edge_cases()


if __name__ == "__main__":
    main()
