"""ZIP / DOCX artifacts for the pipeline (P1 prototype).

Uses the same search and backtracking as PNG, with the ZIP validator plugged
in. The ML ranker was trained on PNG only, so ZIP always uses the baseline
(nearest block first) ranker.

Evidence states:
  every entry + central directory + EOCD verified   -> PROVEN
  at least one entry's CRC-32 and size verified      -> PARTIAL
  nothing verified, or an unsupported ZIP feature    -> REJECTED
"""
import hashlib
import html
import re
from typing import List, Tuple

from core.carve import classify, zip_type
from core.ingest import Image
from core.pipeline import _runs, assembly_bytes, build_trail
from core.search import BaselineRanker, ReconStatus, ReconstructionResult, reconstruct_png
from core.triage import triage
from core.validate_png import Status
from core.validate_zip import ZipValidationResult, validate_zip

TEXT_PREVIEW_CHARS = 3000


def zip_prefix_bytes(result: ZipValidationResult) -> int:
    """Bytes up to the end of the last entry whose CRC-32 and size matched."""
    return result.verified_bytes


def reconstruct_zip(image: Image, anchor) -> ReconstructionResult:
    return reconstruct_png(image, anchor, ranker=BaselineRanker(image),
                           validator=validate_zip, prefix_bytes=zip_prefix_bytes)


def verified_prefix(image: Image, recon: ReconstructionResult) -> Tuple[List[int], bytes]:
    """Longest run of verified entries any attempt reached, cut at that entry's end.

    Each entry is checked on its own (CRC-32 of its uncompressed data), so
    entries verified before a later failure are still verified.
    """
    paths = [list(recon.path) or [recon.anchor_block]]
    paths += [list(e.path) for e in recon.events if e.event == "VALIDATION_RESULT"]
    best_path, best_bytes = paths[0], -1
    for path in paths:
        count = validate_zip(assembly_bytes(image, path)).verified_bytes
        if count > best_bytes:
            best_path, best_bytes = path, count
    blocks, covered = [], 0
    for block in best_path:
        if blocks and covered >= best_bytes:
            break
        blocks.append(block)
        covered += image.blocks[block].length
    return blocks, assembly_bytes(image, blocks)[:best_bytes]


def docx_text(result: ZipValidationResult) -> str:
    """Plain text of word/document.xml, if that entry verified. One line per paragraph."""
    for entry, data in zip(result.entries, result.entry_data):
        if entry.name == "word/document.xml":
            xml = data.decode("utf-8", errors="replace")
            paragraphs = []
            for para in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
                words = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)
                paragraphs.append(html.unescape("".join(words)))
            text = "\n".join(paragraphs)
            return text[:TEXT_PREVIEW_CHARS] + ("…" if len(text) > TEXT_PREVIEW_CHARS else "")
    return ""


def _checks(result: ZipValidationResult) -> List[dict]:
    checks = [{"check": "signature", "ok": result.reason_code != "signature_mismatch"}]
    for e in result.entries:
        checks.append({"check": f"{e.name}_crc", "stored": f"0x{e.stored_crc:08X}",
                       "computed": f"0x{e.computed_crc:08X}", "ok": e.stored_crc == e.computed_crc})
        checks.append({"check": f"{e.name}_size", "actual": e.actual_size,
                       "expected": e.stored_size, "ok": e.actual_size == e.stored_size})
    checks.append({"check": "central_directory", "ok": result.central_directory_ok})
    checks.append({"check": "EOCD", "ok": True if result.eocd_reached else None})
    return checks


def _verify_events(image: Image, step: int, path: List[int]) -> List[dict]:
    """Name the entries the last block completed, or the check it broke."""
    data = assembly_bytes(image, path)
    result = validate_zip(data)
    new_from = len(data) - image.blocks[path[-1]].length if len(path) > 1 else 0
    events = [{"step": step, "event": "VERIFY_OK", "chunk": e.name}
              for e in result.entries if e.ok and e.end > new_from]
    if result.status is Status.VALID:
        events.append({"step": step, "event": "VERIFY_OK", "chunk": "zip_final_checks"})
    elif result.status is Status.INVALID:
        reason = "entry_zlib_error" if result.reason_code == "zlib_error" else result.reason_code
        events.append({"step": step, "event": "VERIFY_FAIL", "chunk": result.stage, "reason": reason})
    return events


def _explain(state: str, recon: ReconstructionResult, result: ZipValidationResult,
             assembly: List[int], verified: int, unsupported: bool) -> str:
    names = ", ".join(e.name for e in result.entries)
    if unsupported:
        return ("The archive uses a ZIP feature this prototype does not verify "
                "(data descriptor, ZIP64 or encryption), so nothing is reported as evidence.")
    if state == "PROVEN":
        runs = _runs(assembly)
        layout = "one contiguous run" if runs == 1 else f"{runs} separate fragments"
        return (f"All {len(result.entries)} entries matched their stored CRC-32 and size, the "
                f"central directory agrees with every local header, and the EOCD was reached. "
                f"Assembled from {len(assembly)} blocks in {layout}; the search backtracked "
                f"{recon.backtrack_count} time(s).")
    if state == "REJECTED":
        return f"No entry could be verified from anchor block {recon.anchor_block}."
    frozen = list(recon.path) or [recon.anchor_block]
    return (f"Verified up to byte {verified}: {len(result.entries)} complete entr"
            f"{'y' if len(result.entries) == 1 else 'ies'} ({names}). No complete continuation "
            f"was found after block {frozen[-1]}. The total size is unknown until the central "
            f"directory is reached. The data needed after byte {verified} is missing, corrupted, "
            f"or not found by the search; the system cannot tell which.")


def build_zip_artifact(image: Image, recon: ReconstructionResult, art_id: str) -> dict:
    unsupported = any(e.event == "VALIDATION_RESULT" and e.validator_reason == "unsupported_zip_feature"
                      for e in recon.events)
    if recon.status is ReconStatus.VERIFIED and not unsupported:
        state = "PROVEN"
        assembly = list(recon.path)
        data = recon.reconstructed_bytes
        result = recon.final_validator
        completeness = 1.0
    else:
        assembly, data = verified_prefix(image, recon)
        result = validate_zip(data)
        state = "PARTIAL" if result.entries and not unsupported else "REJECTED"
        completeness = 0.0            # total size unknown without the central directory
        if unsupported:
            assembly, data = [recon.anchor_block], b""
    verified = len(data)
    kind = classify(zip_type([e.name for e in result.entries]))
    return {
        "id": art_id,
        "type": kind["type"],
        "category": kind["category"],
        "anchor_block": recon.anchor_block,
        "width": None,
        "height": None,
        "assembly": assembly,
        "state": state,
        "verified_bytes": verified,
        "expected_bytes": verified if state == "PROVEN" else None,
        "unverifiable_from": None if state == "PROVEN" else verified,
        "sha256": hashlib.sha256(data).hexdigest() if verified else None,
        "attempts": recon.validation_count,
        "search_status": recon.status.value,
        "ranker_used": "baseline",
        "explanation": _explain(state, recon, result, assembly, verified, unsupported),
        "entries": [{"name": e.name, "size": e.stored_size, "ok": e.ok} for e in result.entries],
        "text_preview": docx_text(result) if not unsupported else "",
        "checks": _checks(result),
        "trail": build_trail(image, recon, verify_events=_verify_events),
        "triage": triage(state, completeness, kind["category"]),
    }
