"""Format-aware ZIP / DOCX validator for VERDICT (CLAUDE.md §6, P1 prototype).

Same contract as validate_png: VALID, INCOMPLETE or INVALID for a candidate buffer.

A ZIP file is a run of entries, each [local file header][compressed data],
followed by a central directory (one header per entry) and an End of Central
Directory record (EOCD), which is the terminator.

Each entry is its own verified unit: its local header stores the CRC-32 and
size of the UNCOMPRESSED data, so once an entry's bytes are all present we
inflate them and compare. Inflation is streamed, so a zlib error on a wrong
block fails early, before the entry is complete.

PROVEN needs every entry verified, a central directory that matches every
local header, and the EOCD. CRC-32 is not cryptographic: it catches accidental
corruption and wrong assembly, not deliberate forgery.

Not supported (INVALID, reason "unsupported_zip_feature"): data descriptors
(flag bit 3, sizes stored after the data), ZIP64, encryption, and compression
methods other than stored (0) and deflate (8).
"""
import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Optional

from core.validate_png import Status

LOCAL_SIG = b"PK\x03\x04"
CENTRAL_SIG = b"PK\x01\x02"
EOCD_SIG = b"PK\x05\x06"
LOCAL_HEADER_SIZE = 30
CENTRAL_HEADER_SIZE = 46
EOCD_SIZE = 22
MAX_NAME_LENGTH = 1024
ZIP64_EXTRA_ID = 0x0001


@dataclass
class EntryCheck:
    """One complete entry: its stored CRC-32 / size against what inflation produced."""
    name: str
    offset: int              # start of the local header
    end: int                 # first byte after the compressed data
    method: int
    compressed_size: int
    stored_size: int
    actual_size: int
    stored_crc: int
    computed_crc: int
    ok: bool


@dataclass
class ZipValidationResult:
    status: Status
    reason_code: str
    reason: str
    stage: str = "local_header"
    consumed_length: int = 0         # bytes up to the end of the EOCD (VALID only)
    verified_bytes: int = 0          # end of the last entry whose CRC-32 and size matched
    entries: List[EntryCheck] = field(default_factory=list)
    entry_data: List[bytes] = field(default_factory=list)   # inflated bytes of verified entries
    central_directory_ok: Optional[bool] = None
    eocd_reached: bool = False


def _result(res: ZipValidationResult, status: Status, code: str, reason: str) -> ZipValidationResult:
    res.status, res.reason_code, res.reason = status, code, reason
    return res


def _has_zip64_extra(extra: bytes) -> bool:
    pos = 0
    while pos + 4 <= len(extra):
        header_id, size = struct.unpack_from("<HH", extra, pos)
        if header_id == ZIP64_EXTRA_ID:
            return True
        pos += 4 + size
    return False


def _sane_name(raw: bytes) -> bool:
    return 0 < len(raw) <= MAX_NAME_LENGTH and all(b >= 0x20 and b != 0x7F for b in raw)


def validate_zip(data: bytes) -> ZipValidationResult:
    """Validate a candidate ZIP buffer that starts at a local file header."""
    res = ZipValidationResult(status=Status.INCOMPLETE, reason_code="", reason="")
    pos = 0
    while True:
        if len(data) - pos < 4:
            return _result(res, Status.INCOMPLETE, "need_more_bytes", "next record not present yet")
        sig = data[pos:pos + 4]
        if sig == LOCAL_SIG:
            outcome = _check_entry(data, pos, res)
            if outcome is not None:
                return outcome
            pos = res.entries[-1].end
            res.verified_bytes = pos
        elif sig == CENTRAL_SIG and res.entries:
            return _check_central_directory(data, pos, res)
        elif pos == 0:
            return _result(res, Status.INVALID, "signature_mismatch", "no local file header at offset 0")
        else:
            return _result(res, Status.INVALID, "unexpected_signature",
                           f"bytes at offset {pos} are neither an entry nor the central directory")


def _check_entry(data: bytes, pos: int, res: ZipValidationResult) -> Optional[ZipValidationResult]:
    """Check one entry. Returns a finished result on failure/INCOMPLETE, None once verified."""
    res.stage = "local_header"
    if len(data) - pos < LOCAL_HEADER_SIZE:
        return _result(res, Status.INCOMPLETE, "need_more_bytes", "local header not complete")
    (flag, method, crc, csize, usize,
     name_len, extra_len) = struct.unpack_from("<2xHH4xIIIHH", data, pos + 4)
    if flag & 0x08 or flag & 0x01:
        return _result(res, Status.INVALID, "unsupported_zip_feature",
                        "data descriptor or encryption (flag bits 3 / 0)")
    if csize == 0xFFFFFFFF or usize == 0xFFFFFFFF:
        return _result(res, Status.INVALID, "unsupported_zip_feature", "ZIP64 sizes")
    if method not in (0, 8):
        return _result(res, Status.INVALID, "unsupported_zip_feature", f"compression method {method}")
    data_start = pos + LOCAL_HEADER_SIZE + name_len + extra_len
    if len(data) < data_start:
        return _result(res, Status.INCOMPLETE, "need_more_bytes", "file name not complete")
    raw_name = data[pos + LOCAL_HEADER_SIZE:pos + LOCAL_HEADER_SIZE + name_len]
    if not _sane_name(raw_name):
        return _result(res, Status.INVALID, "local_header_invalid", f"implausible file name at offset {pos}")
    if _has_zip64_extra(data[pos + LOCAL_HEADER_SIZE + name_len:data_start]):
        return _result(res, Status.INVALID, "unsupported_zip_feature", "ZIP64 extra field")
    name = raw_name.decode("utf-8" if flag & 0x800 else "cp437")

    res.stage = f"entry:{name}"
    present = data[data_start:data_start + csize]
    if method == 8:
        inflater = zlib.decompressobj(-15)
        try:
            inflated = inflater.decompress(present, usize + 1)
        except zlib.error:
            return _result(res, Status.INVALID, "zlib_error", f"{name}: compressed data did not inflate")
    else:
        inflated = present
    if len(inflated) > usize:
        return _result(res, Status.INVALID, "entry_size_mismatch", f"{name}: more data than the header declares")
    if len(present) < csize:
        return _result(res, Status.INCOMPLETE, "need_more_bytes", f"{name}: data not complete")
    if method == 8 and not (inflater.eof and not inflater.unused_data):
        return _result(res, Status.INVALID, "zlib_error", f"{name}: deflate stream does not end where the header says")

    computed = zlib.crc32(inflated) & 0xFFFFFFFF
    ok = computed == crc and len(inflated) == usize
    res.entries.append(EntryCheck(name=name, offset=pos, end=data_start + csize, method=method,
                                  compressed_size=csize, stored_size=usize, actual_size=len(inflated),
                                  stored_crc=crc, computed_crc=computed, ok=ok))
    if len(inflated) != usize:
        return _result(res, Status.INVALID, "entry_size_mismatch", f"{name}: inflated size differs from header")
    if computed != crc:
        return _result(res, Status.INVALID, "entry_crc_mismatch", f"{name}: CRC-32 mismatch")
    res.entry_data.append(inflated)
    return None


def _check_central_directory(data: bytes, cd_start: int, res: ZipValidationResult) -> ZipValidationResult:
    """Every central header must match its local header; then the EOCD ends the file."""
    res.stage = "central_directory"
    pos = cd_start
    for entry in res.entries:
        if len(data) - pos < CENTRAL_HEADER_SIZE:
            return _result(res, Status.INCOMPLETE, "need_more_bytes", "central directory not complete")
        if data[pos:pos + 4] != CENTRAL_SIG:
            res.central_directory_ok = False
            return _result(res, Status.INVALID, "central_directory_mismatch",
                           f"expected a central header for {entry.name}")
        (flag, method, crc, csize, usize, name_len, extra_len,
         comment_len, local_offset) = struct.unpack_from("<8xHH4xIIIHHH8xI", data, pos)
        name_end = pos + CENTRAL_HEADER_SIZE + name_len
        if len(data) < name_end:
            return _result(res, Status.INCOMPLETE, "need_more_bytes", "central directory not complete")
        raw_name = data[pos + CENTRAL_HEADER_SIZE:name_end]
        name = raw_name.decode("utf-8" if flag & 0x800 else "cp437", errors="replace")
        if (name, method, crc, csize, usize, local_offset) != (
                entry.name, entry.method, entry.stored_crc, entry.compressed_size,
                entry.stored_size, entry.offset):
            res.central_directory_ok = False
            return _result(res, Status.INVALID, "central_directory_mismatch",
                           f"central header for {entry.name} disagrees with its local header")
        pos = name_end + extra_len + comment_len
    cd_size = pos - cd_start

    res.stage = "eocd"
    if len(data) - pos < EOCD_SIZE:
        return _result(res, Status.INCOMPLETE, "need_more_bytes", "EOCD not complete")
    if data[pos:pos + 4] != EOCD_SIG:
        res.central_directory_ok = False
        return _result(res, Status.INVALID, "central_directory_mismatch",
                       "more central headers than entries, or EOCD missing")
    res.central_directory_ok = True
    disk, cd_disk, on_disk, total, size, offset, comment_len = struct.unpack_from("<HHHHIIH", data, pos + 4)
    if (disk, cd_disk, on_disk, total, size, offset) != (0, 0, len(res.entries), len(res.entries),
                                                         cd_size, cd_start):
        return _result(res, Status.INVALID, "eocd_mismatch", "EOCD counts or offsets disagree")
    end = pos + EOCD_SIZE + comment_len
    if len(data) < end:
        return _result(res, Status.INCOMPLETE, "need_more_bytes", "EOCD comment not complete")
    res.eocd_reached = True
    res.consumed_length = end
    res.verified_bytes = end
    return _result(res, Status.VALID, "ok", "every entry, the central directory and the EOCD verified")
