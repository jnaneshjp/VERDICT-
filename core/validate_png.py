"""Format-aware PNG validator for VERDICT (CLAUDE.md §6, §20 task 3).

Deterministic oracle over bytes: returns VALID, INCOMPLETE, or INVALID for a
candidate PNG buffer. The future reconstruction/search code will call this
repeatedly on growing candidates:

    INVALID    -> reject/backtrack
    INCOMPLETE -> add another block and re-check
    VALID      -> full PNG reached IEND with all format checks passed

Nothing here knows about blocks, ranking, ML, ground-source data, or search.
The verdict is derived only from the supplied bytes and the PNG format rules.
"""
import math
import struct
import zlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHUNK_HEADER_SIZE = 8              # length (4) + type (4)
CHUNK_CRC_SIZE = 4
MAX_CHUNK_LENGTH = (1 << 31) - 1   # PNG spec upper bound; also a safety cap

# Legal (color_type -> {bit_depth}) combinations per PNG spec (table 11.1).
_LEGAL_BIT_DEPTHS: Dict[int, set] = {
    0: {1, 2, 4, 8, 16},   # greyscale
    2: {8, 16},            # RGB
    3: {1, 2, 4, 8},       # palette
    4: {8, 16},            # greyscale + alpha
    6: {8, 16},            # RGB + alpha
}
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class Status(str, Enum):
    VALID = "VALID"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


@dataclass
class ChunkCheck:
    """One completed chunk's CRC verification (used by the Proof Panel later)."""
    index: int
    type: str
    offset: int
    length: int
    stored_crc: int
    computed_crc: int
    ok: bool


@dataclass
class PNGValidationResult:
    """Structured verdict + evidence for a single validate_png() call."""
    status: Status
    reason_code: str
    reason: str
    stage: str = "signature"
    bytes_examined: int = 0
    consumed_length: int = 0
    chunks_parsed: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    bit_depth: Optional[int] = None
    color_type: Optional[int] = None
    compression_method: Optional[int] = None
    filter_method: Optional[int] = None
    interlace_method: Optional[int] = None
    idat_chunk_count: int = 0
    compressed_idat_size: int = 0
    idat_ranges: List[Tuple[int, int]] = field(default_factory=list)
    chunk_checks: List[ChunkCheck] = field(default_factory=list)
    zlib_ok: Optional[bool] = None
    inflated_length: Optional[int] = None
    expected_inflated_length: Optional[int] = None
    iend_reached: bool = False
    supported: bool = True
    pillow_ok: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["status"] = self.status.value
        return out


def _is_ascii_letter(byte: int) -> bool:
    return 0x41 <= byte <= 0x5A or 0x61 <= byte <= 0x7A


def _valid_chunk_type(chunk_type: bytes) -> bool:
    return len(chunk_type) == 4 and all(_is_ascii_letter(b) for b in chunk_type)


def _row_bytes(width: int, color_type: int, bit_depth: int) -> int:
    """Bytes per scanline including the leading filter byte (non-interlaced only)."""
    bits_per_pixel = _CHANNELS[color_type] * bit_depth
    return 1 + math.ceil(width * bits_per_pixel / 8)


def validate_png(data: bytes, *, use_pillow: bool = False) -> PNGValidationResult:
    """Deterministic PNG validator: VALID, INCOMPLETE, or INVALID."""
    total = len(data)
    result = PNGValidationResult(status=Status.INCOMPLETE, reason_code="", reason="")

    # -------------------------------------------------------------- signature
    if total < len(PNG_SIGNATURE):
        if data == PNG_SIGNATURE[:total]:
            result.stage = "signature"
            result.bytes_examined = total
            result.reason_code = "signature_incomplete"
            result.reason = f"only {total} of 8 PNG signature bytes supplied"
            return result
        result.status = Status.INVALID
        result.stage = "signature"
        result.bytes_examined = total
        result.reason_code = "signature_mismatch"
        result.reason = "leading bytes do not match the PNG signature"
        return result
    if data[:len(PNG_SIGNATURE)] != PNG_SIGNATURE:
        result.status = Status.INVALID
        result.stage = "signature"
        result.bytes_examined = 8
        result.reason_code = "signature_mismatch"
        result.reason = "leading bytes do not match the PNG signature"
        return result

    result.stage = "signature_ok"
    result.bytes_examined = len(PNG_SIGNATURE)
    pos = len(PNG_SIGNATURE)

    idat_seen = False
    idat_ended = False
    plte_seen = False
    idat_data_parts: List[bytes] = []

    # -------------------------------------------------------------- chunk loop
    while True:
        chunk_start = pos

        # Need chunk length
        if pos + 4 > total:
            result.stage = "chunk_length"
            result.bytes_examined = pos
            result.reason_code = "chunk_length_truncated"
            result.reason = "not enough bytes for the next chunk length field"
            return result
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        if length > MAX_CHUNK_LENGTH:
            result.status = Status.INVALID
            result.stage = "chunk_length"
            result.bytes_examined = pos + 4
            result.reason_code = "chunk_length_too_large"
            result.reason = f"chunk length {length} exceeds PNG maximum {MAX_CHUNK_LENGTH}"
            return result

        # Need chunk type
        if pos + 8 > total:
            result.stage = "chunk_type"
            result.bytes_examined = pos
            result.reason_code = "chunk_type_truncated"
            result.reason = "not enough bytes for the chunk type field"
            return result
        chunk_type = data[pos + 4:pos + 8]
        type_str = chunk_type.decode("ascii", errors="replace")
        if not _valid_chunk_type(chunk_type):
            result.status = Status.INVALID
            result.stage = f"chunk_{result.chunks_parsed}_type"
            result.bytes_examined = pos + 8
            result.reason_code = "chunk_type_invalid"
            result.reason = f"chunk type {chunk_type!r} is not four ASCII letters"
            return result

        # Need chunk data + CRC
        end = pos + 8 + length + CHUNK_CRC_SIZE
        if end > total:
            result.stage = f"chunk_{type_str}_body"
            result.bytes_examined = pos
            result.reason_code = "chunk_body_truncated"
            result.reason = (
                f"{type_str} chunk needs {end - total} more bytes for data+CRC "
                f"(declared length {length})"
            )
            return result

        chunk_data = data[pos + 8:pos + 8 + length]
        stored_crc = struct.unpack(">I", data[pos + 8 + length:end])[0]
        computed_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        crc_ok = stored_crc == computed_crc
        result.chunk_checks.append(ChunkCheck(
            index=result.chunks_parsed,
            type=type_str,
            offset=chunk_start,
            length=length,
            stored_crc=stored_crc,
            computed_crc=computed_crc,
            ok=crc_ok,
        ))
        result.bytes_examined = end

        if not crc_ok:
            result.status = Status.INVALID
            result.stage = f"chunk_{type_str}_crc"
            result.reason_code = "chunk_crc_mismatch"
            result.reason = (
                f"{type_str} chunk #{result.chunks_parsed}: CRC mismatch "
                f"(stored 0x{stored_crc:08X}, computed 0x{computed_crc:08X})"
            )
            return result

        # ---- per-type handling (all with CRC already verified) --------------
        if result.chunks_parsed == 0:
            if type_str != "IHDR":
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_not_first"
                result.reason = f"first chunk is {type_str}, expected IHDR"
                return result
            if length != 13:
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_length"
                result.reason = f"IHDR length is {length}, expected 13"
                return result
            (width, height, bit_depth, color_type,
             comp_method, filter_method, interlace_method) = struct.unpack(">IIBBBBB", chunk_data)
            result.width = width
            result.height = height
            result.bit_depth = bit_depth
            result.color_type = color_type
            result.compression_method = comp_method
            result.filter_method = filter_method
            result.interlace_method = interlace_method
            if width == 0 or height == 0:
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_dimensions"
                result.reason = f"IHDR dimensions must be > 0 (got {width}x{height})"
                return result
            if comp_method != 0:
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_compression_method"
                result.reason = f"IHDR compression method {comp_method}: only 0 is legal"
                return result
            if filter_method != 0:
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_filter_method"
                result.reason = f"IHDR filter method {filter_method}: only 0 is legal"
                return result
            if interlace_method not in (0, 1):
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_interlace_method"
                result.reason = f"IHDR interlace method {interlace_method}: must be 0 or 1"
                return result
            if color_type not in _LEGAL_BIT_DEPTHS:
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_color_type"
                result.reason = f"IHDR color type {color_type} is not a legal PNG value"
                return result
            if bit_depth not in _LEGAL_BIT_DEPTHS[color_type]:
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "ihdr_bit_depth_combo"
                result.reason = (
                    f"IHDR bit depth {bit_depth} is illegal for color type {color_type}"
                )
                return result
            if interlace_method == 1:
                # P0 supports non-interlaced only. Adam7 needs a different size formula.
                result.supported = False
                result.status = Status.INVALID
                result.stage = "ihdr"
                result.reason_code = "unsupported_interlace"
                result.reason = "interlaced (Adam7) PNGs are not in the P0 supported subset"
                return result
        else:
            if type_str == "IHDR":
                result.status = Status.INVALID
                result.stage = f"chunk_{result.chunks_parsed}"
                result.reason_code = "ihdr_duplicate"
                result.reason = "IHDR appeared after the first chunk"
                return result

            if type_str == "IDAT":
                if idat_ended:
                    result.status = Status.INVALID
                    result.stage = "idat_order"
                    result.reason_code = "idat_not_consecutive"
                    result.reason = "IDAT chunk appeared after a non-IDAT chunk"
                    return result
                idat_seen = True
                result.idat_chunk_count += 1
                result.compressed_idat_size += length
                result.idat_ranges.append((pos + 8, pos + 8 + length))
                idat_data_parts.append(bytes(chunk_data))
            else:
                if idat_seen:
                    idat_ended = True

            if type_str == "PLTE":
                if idat_seen:
                    result.status = Status.INVALID
                    result.stage = "plte_order"
                    result.reason_code = "plte_after_idat"
                    result.reason = "PLTE chunk must precede IDAT"
                    return result
                if length == 0 or length % 3 != 0:
                    result.status = Status.INVALID
                    result.stage = "plte"
                    result.reason_code = "plte_length"
                    result.reason = f"PLTE length {length} must be a non-zero multiple of 3"
                    return result
                plte_seen = True

            if type_str == "IEND":
                if length != 0:
                    result.status = Status.INVALID
                    result.stage = "iend"
                    result.reason_code = "iend_length"
                    result.reason = f"IEND length is {length}, expected 0"
                    return result
                if not idat_seen:
                    result.status = Status.INVALID
                    result.stage = "iend"
                    result.reason_code = "no_idat"
                    result.reason = "no IDAT chunk before IEND"
                    return result
                if result.color_type == 3 and not plte_seen:
                    result.status = Status.INVALID
                    result.stage = "iend"
                    result.reason_code = "missing_plte"
                    result.reason = "palette color type requires a PLTE chunk before IDAT"
                    return result

                compressed = b"".join(idat_data_parts)
                try:
                    inflated = zlib.decompress(compressed)
                except zlib.error as exc:
                    result.status = Status.INVALID
                    result.stage = "zlib"
                    result.zlib_ok = False
                    result.reason_code = "zlib_error"
                    result.reason = f"zlib decompression failed: {exc}"
                    return result
                result.zlib_ok = True
                result.inflated_length = len(inflated)

                # Structural image-data check (non-interlaced only; Adam7 rejected above)
                expected = _row_bytes(result.width, result.color_type, result.bit_depth) * result.height
                result.expected_inflated_length = expected
                if len(inflated) != expected:
                    result.status = Status.INVALID
                    result.stage = "image_data"
                    result.reason_code = "inflated_length_mismatch"
                    result.reason = (
                        f"inflated data length {len(inflated)} != expected {expected} "
                        f"(width={result.width}, height={result.height}, "
                        f"color_type={result.color_type}, bit_depth={result.bit_depth})"
                    )
                    return result

                result.chunks_parsed += 1
                result.consumed_length = end
                result.iend_reached = True
                result.stage = "complete"
                result.status = Status.VALID
                result.reason_code = "valid"
                result.reason = "PNG signature, chunk framing, CRCs, zlib and image-data checks all passed"

                if use_pillow:
                    result.pillow_ok = _pillow_supplementary(data[:end])
                return result

        result.chunks_parsed += 1
        pos = end
        # loop continues to next chunk


def _pillow_supplementary(png_bytes: bytes) -> bool:
    """Optional decoder-level cross-check. Never used to decide VALID on its own."""
    try:
        from io import BytesIO
        from PIL import Image
        image = Image.open(BytesIO(png_bytes))
        image.load()
        return True
    except Exception:
        return False
