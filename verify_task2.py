"""Task-2 verification harness for VERDICT (CLAUDE.md §20 task 2).

Runs ingest + carve on data/cases/<case>.img, prints image / block / anchor
metadata, checks ingest invariants, exercises edge cases, then loads
data/truth/<case>.json (post-hoc, evaluation only) to compare discovered PNG
anchors against known ones. No file reconstruction or evidence claims here.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.config import BLOCK_SIZE  # noqa: E402
from core.carve import PNG_SIGNATURE, carve_png_anchors, find_png_anchors  # noqa: E402
from core.ingest import ingest_bytes, ingest_image  # noqa: E402


def check_ingest_invariants(image, raw):
    joined = b"".join(b.data for b in image.blocks)
    assert joined == raw, "concatenated block bytes differ from original"
    assert image.raw == raw, "image.raw differs from original"
    assert sum(b.length for b in image.blocks) == image.size, "block length sum != image size"
    for block in image.blocks:
        assert block.offset == block.index * image.block_size, f"block {block.index}: wrong offset"
        assert len(block.data) == block.length, f"block {block.index}: length field wrong"
        assert hashlib.sha256(block.data).hexdigest() == block.sha256, f"block {block.index}: sha256 wrong"


def run_edge_cases():
    print("\n[edge cases]")

    empty = ingest_bytes(b"")
    assert empty.size == 0 and empty.block_count == 0 and empty.blocks == []
    assert find_png_anchors(b"") == []
    print("  empty image                     block_count=0, no anchors                 ok")

    tiny = ingest_bytes(b"\x00" * 10)
    assert tiny.block_count == 1 and tiny.blocks[0].length == 10
    print("  image smaller than one block    block_count=1, length=10                  ok")

    partial_size = BLOCK_SIZE * 2 + 17
    partial = ingest_bytes(b"A" * partial_size)
    assert partial.block_count == 3
    assert partial.blocks[-1].length == 17
    assert sum(b.length for b in partial.blocks) == partial_size
    print(f"  non-divisible size ({partial_size} bytes)  blocks=3, last length=17                 ok")

    at_end = bytearray(BLOCK_SIZE * 2)
    at_end[BLOCK_SIZE - 8:BLOCK_SIZE] = PNG_SIGNATURE
    anchors = find_png_anchors(bytes(at_end))
    assert [a.byte_offset for a in anchors] == [BLOCK_SIZE - 8]
    assert anchors[0].block_index == 0
    print(f"  signature at end of block       offset={BLOCK_SIZE - 8}, block=0                    ok")

    crossing = bytearray(BLOCK_SIZE * 2)
    crossing[BLOCK_SIZE - 4:BLOCK_SIZE - 4 + 8] = PNG_SIGNATURE
    anchors = find_png_anchors(bytes(crossing))
    assert [a.byte_offset for a in anchors] == [BLOCK_SIZE - 4]
    assert anchors[0].block_index == 0
    print(f"  signature crossing boundary     offset={BLOCK_SIZE - 4}, block=0                    ok")

    multi = PNG_SIGNATURE + b"garbage" + PNG_SIGNATURE + b"\x00" * 100 + PNG_SIGNATURE
    anchors = find_png_anchors(multi)
    offsets = [a.byte_offset for a in anchors]
    assert offsets == [0, 15, 123], offsets
    print(f"  multiple signatures             offsets={offsets}             ok")

    embedded = b"\x00" * 1000 + PNG_SIGNATURE + b"\x00" * 1000
    anchors = find_png_anchors(embedded)
    assert [a.byte_offset for a in anchors] == [1000]
    print("  signature inside unrelated data offset=1000                               ok")


def main():
    parser = argparse.ArgumentParser(description="Task-2 verification harness.")
    parser.add_argument("--case", default="demo01")
    args = parser.parse_args()

    image_path = os.path.join(ROOT, "data", "cases", f"{args.case}.img")
    truth_path = os.path.join(ROOT, "data", "truth", f"{args.case}.json")

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

    if not os.path.exists(truth_path):
        print(f"\n(no {os.path.relpath(truth_path, ROOT)} — skipping post-hoc evaluation)")
        return

    with open(truth_path) as handle:
        truth = json.load(handle)

    expected_blocks = sorted({f["blocks"][0] for f in truth["files"] if f["type"] == "png"})
    expected = [(b * image.block_size, b) for b in expected_blocks]
    found = [(a.byte_offset, a.block_index) for a in anchors]
    exp_set, found_set = set(expected), set(found)
    matches = sorted(exp_set & found_set)
    missed = sorted(exp_set - found_set)
    false_positives = sorted(found_set - exp_set)

    print(f"\n[post-hoc evaluation vs data/truth/{args.case}.json]")
    print(f"expected png anchors ({len(expected)}):")
    for off, blk in expected:
        print(f"    offset {off:>10}  block {blk}")
    print(f"matches:           {len(matches)}/{len(expected)}")
    print(f"missed:            {len(missed)}")
    for off, blk in missed:
        print(f"    offset {off}  block {blk}")
    print(f"false positives:   {len(false_positives)}")
    for off, blk in false_positives:
        print(f"    offset {off}  block {blk}")


if __name__ == "__main__":
    main()
