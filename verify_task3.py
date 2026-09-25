"""Task-3 verification harness for core/validate_png.py (CLAUDE.md §20 task 3).

Rebuilds a known-good PNG from the generator's disk (via truth blocks —
evaluation-only) and drives the validator through the required VALID /
INCOMPLETE / INVALID scenarios. The validator itself never sees truth.
"""
import io
import json
import os
import struct
import sys
import zlib

from PIL import Image as PillowImage

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.config import BLOCK_SIZE  # noqa: E402
from core.ingest import ingest_image  # noqa: E402
from core.validate_png import PNG_SIGNATURE, Status, validate_png  # noqa: E402


# ------------------------------------------------------- helpers (test only)

def rebuild_known_good(case: str = "demo01"):
    """Assemble a clean live PNG's bytes from truth. Used only in this test."""
    truth_path = os.path.join(ROOT, "data", "truth", f"{case}.json")
    with open(truth_path) as handle:
        truth = json.load(handle)
    image = ingest_image(os.path.join(ROOT, "data", "cases", f"{case}.img"))
    for entry in truth["files"]:
        if (entry["type"] == "png" and entry["status"] == "live"
                and not entry["corrupted_blocks"] and not entry["overwritten_blocks"]):
            raw = b"".join(image.block_data(image.blocks[b]) for b in entry["blocks"])[:entry["size"]]
            return entry, raw
    raise RuntimeError("no clean live PNG in truth")


def _read_length(buf: bytes, offset: int) -> int:
    return struct.unpack(">I", buf[offset:offset + 4])[0]


def find_chunk(png: bytes, chunk_type: bytes) -> int:
    """Byte offset of the first chunk of type `chunk_type` (or -1)."""
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(png):
        length = _read_length(png, pos)
        if png[pos + 4:pos + 8] == chunk_type:
            return pos
        pos = pos + 8 + length + 4
    return -1


def rewrite_chunk(png: bytes, chunk_start: int, chunk_type: bytes, new_data: bytes) -> bytes:
    """Replace one chunk's data (and CRC), keeping surrounding bytes intact."""
    length = _read_length(png, chunk_start)
    old_end = chunk_start + 8 + length + 4
    crc = zlib.crc32(chunk_type + new_data) & 0xFFFFFFFF
    return (
        png[:chunk_start]
        + struct.pack(">I", len(new_data))
        + chunk_type
        + new_data
        + struct.pack(">I", crc)
        + png[old_end:]
    )


# ------------------------------------------------------------------- tests

TESTS = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def expect(result, status, reason_code=None):
    assert result.status == status, (
        f"expected {status.value}, got {result.status.value} "
        f"({result.reason_code}: {result.reason})"
    )
    if reason_code is not None:
        assert result.reason_code == reason_code, (
            f"expected reason_code {reason_code!r}, got {result.reason_code!r} "
            f"({result.reason})"
        )


@test("known-good generated PNG is VALID")
def _(_entry, png):
    r = validate_png(png)
    expect(r, Status.VALID, "valid")
    assert r.consumed_length == len(png)
    assert r.iend_reached and r.zlib_ok


@test("signature prefix (5 bytes) is INCOMPLETE")
def _(_entry, _png):
    expect(validate_png(PNG_SIGNATURE[:5]), Status.INCOMPLETE, "signature_incomplete")


@test("empty buffer is INCOMPLETE (empty prefix of signature)")
def _(_entry, _png):
    expect(validate_png(b""), Status.INCOMPLETE, "signature_incomplete")


@test("wrong signature is INVALID")
def _(_entry, png):
    expect(validate_png(b"NOTPNG!!" + png[8:]), Status.INVALID, "signature_mismatch")


@test("truncated chunk length field is INCOMPLETE")
def _(_entry, png):
    # sig + 2 bytes of the IHDR length field
    expect(validate_png(png[:len(PNG_SIGNATURE) + 2]), Status.INCOMPLETE, "chunk_length_truncated")


@test("truncated chunk type field is INCOMPLETE")
def _(_entry, png):
    # sig + length but not full type
    expect(validate_png(png[:len(PNG_SIGNATURE) + 6]), Status.INCOMPLETE, "chunk_type_truncated")


@test("truncated chunk body is INCOMPLETE")
def _(_entry, png):
    idat_start = find_chunk(png, b"IDAT")
    length = _read_length(png, idat_start)
    expect(validate_png(png[:idat_start + 8 + max(1, length // 2)]),
           Status.INCOMPLETE, "chunk_body_truncated")


@test("corrupted chunk data (CRC unchanged) is INVALID via CRC mismatch")
def _(_entry, png):
    ba = bytearray(png)
    ba[len(PNG_SIGNATURE) + 8] ^= 0x01   # flip a bit in IHDR data
    expect(validate_png(bytes(ba)), Status.INVALID, "chunk_crc_mismatch")


@test("corrupted stored CRC is INVALID via CRC mismatch")
def _(_entry, png):
    ba = bytearray(png)
    ihdr_start = len(PNG_SIGNATURE)
    length = _read_length(ba, ihdr_start)
    ba[ihdr_start + 8 + length] ^= 0xFF  # flip a byte in IHDR CRC
    expect(validate_png(bytes(ba)), Status.INVALID, "chunk_crc_mismatch")


@test("IHDR length != 13 is INVALID (forged matching CRC)")
def _(_entry, png):
    ihdr_start = len(PNG_SIGNATURE)
    # Forge a length-12 IHDR whose CRC matches, so we exercise the ihdr_length check
    truncated_data = png[ihdr_start + 8:ihdr_start + 8 + 12]
    tampered = rewrite_chunk(png, ihdr_start, b"IHDR", truncated_data)
    r = validate_png(tampered)
    expect(r, Status.INVALID, "ihdr_length")


@test("invalid IHDR field combination is INVALID")
def _(_entry, png):
    ihdr_start = len(PNG_SIGNATURE)
    ihdr_data = png[ihdr_start + 8:ihdr_start + 8 + 13]
    w, h, _bd, _ct, comp, filt, il = struct.unpack(">IIBBBBB", ihdr_data)
    new_data = struct.pack(">IIBBBBB", w, h, 1, 2, comp, filt, il)  # bit_depth 1, RGB → illegal
    tampered = rewrite_chunk(png, ihdr_start, b"IHDR", new_data)
    expect(validate_png(tampered), Status.INVALID, "ihdr_bit_depth_combo")


@test("interlaced PNG (unsupported) is INVALID")
def _(_entry, png):
    ihdr_start = len(PNG_SIGNATURE)
    ihdr_data = png[ihdr_start + 8:ihdr_start + 8 + 13]
    w, h, bd, ct, comp, filt, _il = struct.unpack(">IIBBBBB", ihdr_data)
    new_data = struct.pack(">IIBBBBB", w, h, bd, ct, comp, filt, 1)
    tampered = rewrite_chunk(png, ihdr_start, b"IHDR", new_data)
    expect(validate_png(tampered), Status.INVALID, "unsupported_interlace")


@test("truncated before IEND is INCOMPLETE")
def _(_entry, png):
    iend_start = find_chunk(png, b"IEND")
    expect(validate_png(png[:iend_start]), Status.INCOMPLETE)


@test("IEND length != 0 (with matching CRC) is INVALID")
def _(_entry, png):
    iend_start = find_chunk(png, b"IEND")
    tampered = rewrite_chunk(png, iend_start, b"IEND", b"XXXX")
    expect(validate_png(tampered), Status.INVALID, "iend_length")


@test("valid PNG with trailing garbage: VALID, consumed_length points after IEND")
def _(_entry, png):
    trailer = b"garbage after IEND ---- more bytes -----"
    r = validate_png(png + trailer)
    expect(r, Status.VALID, "valid")
    assert r.consumed_length == len(png), f"consumed_length {r.consumed_length} != {len(png)}"


@test("invalid chunk ordering (IDAT before IHDR) is INVALID")
def _(_entry, png):
    ihdr_start = len(PNG_SIGNATURE)
    ihdr_len = _read_length(png, ihdr_start)
    ihdr_end = ihdr_start + 8 + ihdr_len + 4
    idat_start = ihdr_end
    idat_len = _read_length(png, idat_start)
    idat_end = idat_start + 8 + idat_len + 4
    swapped = (png[:ihdr_start]
               + png[idat_start:idat_end]
               + png[ihdr_start:idat_start]
               + png[idat_end:])
    expect(validate_png(swapped), Status.INVALID, "ihdr_not_first")


@test("known-good PNG has multiple IDAT chunks (per generator)")
def _(_entry, png):
    r = validate_png(png)
    expect(r, Status.VALID)
    assert r.idat_chunk_count >= 2, f"expected >=2 IDATs, got {r.idat_chunk_count}"


@test("corrupted IDAT compressed stream is INVALID via zlib error")
def _(_entry, png):
    idat_start = find_chunk(png, b"IDAT")
    length = _read_length(png, idat_start)
    old_data = bytearray(png[idat_start + 8:idat_start + 8 + length])
    old_data[5] ^= 0xFF   # corrupt deep inside the compressed data
    tampered = rewrite_chunk(png, idat_start, b"IDAT", bytes(old_data))
    r = validate_png(tampered)
    expect(r, Status.INVALID, "zlib_error")


@test("structurally wrong decompressed length is INVALID")
def _(_entry, png):
    # Bump height in IHDR (with matching CRC) so expected_inflated_length no longer matches
    ihdr_start = len(PNG_SIGNATURE)
    ihdr_data = png[ihdr_start + 8:ihdr_start + 8 + 13]
    w, h, bd, ct, comp, filt, il = struct.unpack(">IIBBBBB", ihdr_data)
    tampered_ihdr = struct.pack(">IIBBBBB", w, h + 1, bd, ct, comp, filt, il)
    tampered = rewrite_chunk(png, ihdr_start, b"IHDR", tampered_ihdr)
    expect(validate_png(tampered), Status.INVALID, "inflated_length_mismatch")


@test("prefix ending inside the next chunk's length field is INCOMPLETE")
def _(_entry, png):
    ihdr_start = len(PNG_SIGNATURE)
    ihdr_end = ihdr_start + 8 + _read_length(png, ihdr_start) + 4
    prefix = png[:ihdr_end + 2]     # only 2 of the next chunk's 4 length bytes
    expect(validate_png(prefix), Status.INCOMPLETE, "chunk_length_truncated")


@test("validator populates full IHDR + inflated info for a valid PNG")
def _(_entry, png):
    r = validate_png(png, use_pillow=True)
    expect(r, Status.VALID)
    w, h = PillowImage.open(io.BytesIO(png)).size
    assert r.width == w and r.height == h
    assert r.bit_depth == 8 and r.color_type == 2 and r.interlace_method == 0
    assert r.compression_method == 0 and r.filter_method == 0
    assert r.zlib_ok is True and r.iend_reached is True
    assert r.expected_inflated_length == r.inflated_length
    assert r.pillow_ok is True
    assert r.chunks_parsed >= 3  # IHDR + IDATs + IEND


# ---------------------------------------------------------------------- main

def main():
    entry, png = rebuild_known_good()
    print(f"loaded known-good PNG: {entry['name']}  size={len(png)}  sha256={entry['sha256']}")
    r = validate_png(png)
    print(f"validate_png(known-good) -> {r.status.value}  reason={r.reason}")
    print(f"  IHDR:   width={r.width}  height={r.height}  bit_depth={r.bit_depth}  "
          f"color_type={r.color_type}  interlace={r.interlace_method}")
    print(f"  chunks_parsed={r.chunks_parsed}  idat_chunks={r.idat_chunk_count}  "
          f"compressed_idat_size={r.compressed_idat_size}")
    print(f"  inflated_length={r.inflated_length}  expected={r.expected_inflated_length}")
    print(f"  iend_reached={r.iend_reached}  consumed_length={r.consumed_length}")

    print()
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn(entry, png)
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERR   {name}: {exc!r}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed / {passed + failed} total")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
