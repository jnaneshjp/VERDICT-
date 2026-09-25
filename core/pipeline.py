"""End-to-end pipeline for VERDICT (CLAUDE.md §3, §14).

ingest -> carve -> search -> evidence state -> classify -> triage -> write JSON

Input is ONLY the storage image path. Output is data/output/<case>.json in the
§14 shape, plus a plain-English `explanation` per artifact.

Evidence states (CLAUDE.md §7), derived from the search result:
  VERIFIED search result                          -> PROVEN
  otherwise, frozen prefix has IHDR + >= 1 IDAT   -> PARTIAL
  otherwise                                       -> REJECTED

Only bytes the search froze (never undone) are re-checked and reported, so
nothing unverified is ever presented as evidence.
"""
import argparse
import hashlib
import json
import os
import zlib
from collections import Counter
from typing import Dict, List, Optional

from core.carve import carve_png_anchors, classify
from core.ingest import Image, ingest_image
from core.rank import make_ranker
from core.search import ReconStatus, ReconstructionResult, reconstruct_png
from core.triage import triage
from core.validate_png import PNGValidationResult, Status, _row_bytes, validate_png

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR = os.path.join(ROOT, "data", "cases")
OUTPUT_DIR = os.path.join(ROOT, "data", "output")

# Plain-English wording for validator failure codes, used in explanations.
REASON_TEXT = {
    "chunk_crc_mismatch": "CRC mismatch",
    "chunk_type_invalid": "next chunk header is not a valid chunk type",
    "chunk_length_too_large": "next chunk declares an impossible length",
    "zlib_error": "image data did not inflate",
    "inflated_length_mismatch": "inflated image size differs from IHDR",
    "idat_not_consecutive": "IDAT chunks not consecutive",
}


# ---------------------------------------------------------------- bytes

def assembly_bytes(image: Image, blocks: List[int]) -> bytes:
    """Concatenate whole blocks in order (anchors sit at offset 0 of a block, §3)."""
    return b"".join(image.block_data(image.blocks[b]) for b in blocks)


# ---------------------------------------------------------------- chunk helpers

def _chunk_names(result: PNGValidationResult) -> List[str]:
    """IHDR, IDAT_1, IDAT_2, ..., IEND — IDATs numbered in order."""
    names, seen = [], Counter()
    for check in result.chunk_checks:
        seen[check.type] += 1
        names.append(f"{check.type}_{seen[check.type]}" if check.type == "IDAT" else check.type)
    return names


def _verified_chunks(result: PNGValidationResult) -> list:
    """Leading chunk checks whose CRC matched (stops at the first failure)."""
    ok = []
    for check in result.chunk_checks:
        if not check.ok:
            break
        ok.append(check)
    return ok


def _verified_byte_count(result: PNGValidationResult) -> int:
    """Bytes covered by the signature plus every leading CRC-verified chunk."""
    if result.reason_code in ("signature_mismatch", "signature_incomplete"):
        return 0
    chunks = _verified_chunks(result)
    if not chunks:
        return 8
    last = chunks[-1]
    return last.offset + 12 + last.length           # length + type + data + CRC


def _inflated_fraction(data: bytes, result: PNGValidationResult) -> float:
    """Share of the IHDR-declared image data that the verified IDAT chunks inflate to."""
    if result.width is None or result.color_type is None:
        return 0.0
    idat = b"".join(data[c.offset + 8:c.offset + 8 + c.length]
                    for c in _verified_chunks(result) if c.type == "IDAT")
    try:
        inflated = zlib.decompressobj().decompress(idat)
    except zlib.error:
        return 0.0
    expected = result.height * _row_bytes(result.width, result.color_type, result.bit_depth)
    return min(len(inflated) / expected, 1.0)


# ---------------------------------------------------------------- checks list

def _checks(result: PNGValidationResult) -> List[dict]:
    """§14 `checks`: one entry per format check. ok=None means 'not reached'."""
    sig_ok = result.reason_code not in ("signature_mismatch", "signature_incomplete")
    checks = [{"check": "signature", "ok": sig_ok}]
    for check, name in zip(result.chunk_checks, _chunk_names(result)):
        checks.append({"check": f"{name}_crc",
                       "stored": f"0x{check.stored_crc:08X}",
                       "computed": f"0x{check.computed_crc:08X}",
                       "ok": check.ok})
    ihdr_ok = None if result.width is None else not result.reason_code.startswith("ihdr")
    checks.append({"check": "IHDR_fields", "ok": ihdr_ok})
    complete = result.status is Status.VALID
    consecutive = False if result.reason_code == "idat_not_consecutive" else (True if complete else None)
    checks.append({"check": "IDAT_consecutive", "ok": consecutive})
    checks.append({"check": "zlib_inflate", "ok": result.zlib_ok})
    length_ok = None
    if result.inflated_length is not None:
        length_ok = result.inflated_length == result.expected_inflated_length
    checks.append({"check": "inflated_length", "ok": length_ok,
                   "actual": result.inflated_length,
                   "expected": result.expected_inflated_length})
    checks.append({"check": "IEND", "ok": True if complete else None})
    return checks


# ---------------------------------------------------------------- trail

def _verify_events(image: Image, step: int, path: List[int]) -> List[dict]:
    """Re-check the attempted path and name the chunks the last block completed or broke."""
    data = assembly_bytes(image, path)
    result = validate_png(data)
    new_from = len(data) - image.blocks[path[-1]].length if len(path) > 1 else 0
    events = []
    for check, name in zip(result.chunk_checks, _chunk_names(result)):
        if check.ok and check.offset + 12 + check.length > new_from:
            events.append({"step": step, "event": "VERIFY_OK", "chunk": name})
    if result.status is Status.VALID:
        events.append({"step": step, "event": "VERIFY_OK", "chunk": "final_checks"})
    elif result.status is Status.INVALID:
        failed = result.chunk_checks[-1] if result.chunk_checks else None
        chunk = _chunk_names(result)[-1] if failed and not failed.ok else result.stage
        events.append({"step": step, "event": "VERIFY_FAIL",
                       "chunk": chunk, "reason": result.reason_code})
    return events


def build_trail(image: Image, recon: ReconstructionResult) -> List[dict]:
    """Translate search events into the §14 Proof Panel trail."""
    trail: List[dict] = []
    ranked_at: Dict[int, List[int]] = {}      # path position -> ranked candidate list
    prev_path: List[int] = []
    for e in recon.events:
        if e.event == "SEARCH_START":
            trail.append({"step": e.step, "event": "START", "block": e.block})
        elif e.event == "CANDIDATE_TRIED":
            ranked_at[len(e.path) - 1] = list(e.ranked)
            trail.append({"step": e.step, "event": "PLACE", "block": e.block,
                          "rank": 1, "ranked": list(e.ranked)})
        elif e.event == "BACKTRACK":
            pos = len(e.path) - 1
            dropped = prev_path[pos] if pos < len(prev_path) else None
            ranked = ranked_at.get(pos, [])
            rank = ranked.index(e.block) + 1 if e.block in ranked else None
            trail.append({"step": e.step, "event": "BACKTRACK", "block": dropped})
            trail.append({"step": e.step, "event": "PLACE", "block": e.block, "rank": rank})
        elif e.event == "VALIDATION_RESULT":
            trail.extend(_verify_events(image, e.step, e.path))
        elif e.event in ("SEARCH_SUCCESS", "SEARCH_EXHAUSTED"):
            trail.append({"step": e.step, "event": "STOP", "reason": recon.status.value})
        prev_path = e.path
    return trail


# ---------------------------------------------------------------- explanation

def _blocks_tried_at(recon: ReconstructionResult, position: int) -> List[int]:
    """Every block the search placed at one path position, in the order tried."""
    tried = []
    for e in recon.events:
        if e.event in ("CANDIDATE_TRIED", "BACKTRACK") and len(e.path) == position + 1:
            tried.append(e.block)
    return tried


def _deepest_failure(image: Image, recon: ReconstructionResult) -> Optional[str]:
    """Describe the longest attempted path that failed validation, e.g.
    'block 41 followed by block 42 broke IDAT_9 (CRC mismatch)'."""
    deepest = None
    for e in recon.events:
        if e.event == "VALIDATION_RESULT" and e.validator_status == "INVALID":
            if deepest is None or len(e.path) > len(deepest.path):
                deepest = e
    if deepest is None or len(deepest.path) < 2:
        return None
    result = validate_png(assembly_bytes(image, deepest.path))
    failed = result.chunk_checks[-1] if result.chunk_checks else None
    chunk = _chunk_names(result)[-1] if failed and not failed.ok else result.stage
    reason = REASON_TEXT.get(result.reason_code, result.reason_code)
    a, b = deepest.path[-2], deepest.path[-1]
    return f"block {a} followed by block {b} broke {chunk} ({reason})"


def _runs(blocks: List[int]) -> int:
    """Number of physically contiguous runs (1 = not fragmented)."""
    return 1 + sum(1 for a, b in zip(blocks, blocks[1:]) if b != a + 1) if blocks else 0


def _explain_proven(recon: ReconstructionResult, result: PNGValidationResult) -> str:
    n = len(result.chunk_checks)
    runs = _runs(recon.path)
    layout = "one contiguous run" if runs == 1 else f"{runs} separate fragments"
    return (f"All {n} chunk CRCs matched, the image data inflated to exactly the size "
            f"IHDR declares, and IEND was reached. Assembled from {len(recon.path)} blocks "
            f"in {layout}; the search backtracked {recon.backtrack_count} time(s).")


def _explain_incomplete(image: Image, recon: ReconstructionResult, assembly: List[int],
                        result: PNGValidationResult, verified: int, fraction: float) -> str:
    if recon.status is ReconStatus.REJECTED_ANCHOR:
        return (f"The anchor block {assembly[0]} fails validation: {result.reason}. "
                f"Nothing beyond the signature can be trusted.")
    chunks = _verified_chunks(result)
    idats = sum(1 for c in chunks if c.type == "IDAT")
    last = assembly[-1]
    tried = _blocks_tried_at(recon, len(assembly))
    head = (f"Verified up to byte {verified}: IHDR + {idats} IDAT chunk(s), "
            f"{fraction:.0%} of the image data. ")
    if not idats:
        head = f"Only {'the signature and IHDR' if chunks else 'the signature'} verified. "
    body = (f"No candidate after block {last} led to a valid continuation "
            f"({len(tried)} tried: {tried}). ")
    deepest = _deepest_failure(image, recon)
    if deepest:
        body += f"Deepest attempt: {deepest}. "
    if recon.status is ReconStatus.EXHAUSTED_BUDGET:
        body += f"The search stopped at its budget of {recon.validation_count} placements. "
    return (head + body + f"The data needed after byte {verified} is missing or corrupted; "
            f"the system cannot tell which.")


# ---------------------------------------------------------------- one artifact

def build_artifact(image: Image, recon: ReconstructionResult, art_id: str,
                   signature: str) -> dict:
    """Turn one search result into the §14 artifact record."""
    if recon.status is ReconStatus.VERIFIED:
        state = "PROVEN"
        assembly = list(recon.path)
        result = recon.final_validator
        data = recon.reconstructed_bytes
        verified = expected = recon.reconstructed_length
        completeness = 1.0
        explanation = _explain_proven(recon, result)
    else:
        assembly = list(recon.path) or [recon.anchor_block]   # frozen blocks only
        data = assembly_bytes(image, assembly)
        result = validate_png(data)
        verified = _verified_byte_count(result)
        expected = None                                       # unknown without the original
        chunk_types = [c.type for c in _verified_chunks(result)]
        has_prefix = chunk_types[:1] == ["IHDR"] and "IDAT" in chunk_types
        state = "PARTIAL" if has_prefix else "REJECTED"
        completeness = _inflated_fraction(data, result) if has_prefix else 0.0
        explanation = _explain_incomplete(image, recon, assembly, result, verified, completeness)
        data = data[:verified]

    kind = classify(signature)
    return {
        "id": art_id,
        "type": kind["type"],
        "category": kind["category"],
        "anchor_block": recon.anchor_block,
        "assembly": assembly,
        "state": state,
        "verified_bytes": verified,
        "expected_bytes": expected,
        "unverifiable_from": None if state == "PROVEN" else verified,
        "sha256": hashlib.sha256(data).hexdigest() if verified else None,
        "attempts": recon.validation_count,
        "search_status": recon.status.value,
        "explanation": explanation,
        "checks": _checks(result),
        "trail": build_trail(image, recon),
        "triage": triage(state, completeness, kind["category"]),
    }


# ---------------------------------------------------------------- whole case

def _unattributed(image: Image, claimed: set) -> List[dict]:
    return [{"block": b.index, "entropy": round(b.entropy, 3),
             "printable": round(b.printable_ratio, 3)}
            for b in image.blocks if b.index not in claimed]


def _duplicates(image: Image) -> List[dict]:
    groups = sorted(image.duplicates().items(), key=lambda kv: kv[1][0])
    return [{"sha256": sha, "blocks": blocks} for sha, blocks in groups]


def analyze_image(image_path: str, case_id: str, ranker_name: str = "baseline") -> dict:
    """Run the full pipeline on one image and return the §14 case dict."""
    image = ingest_image(image_path)
    ranker = make_ranker(image, ranker_name)
    artifacts = []
    for number, anchor in enumerate(carve_png_anchors(image), start=1):
        recon = reconstruct_png(image, anchor, ranker=ranker)
        artifacts.append(build_artifact(image, recon, f"art_{number:03d}", anchor.signature))

    claimed = {b for art in artifacts for b in art["assembly"]}
    duplicates = _duplicates(image)
    states = Counter(art["state"] for art in artifacts)
    summary = {"artifacts": len(artifacts)}
    for state in ("PROVEN", "PLAUSIBLE", "PARTIAL", "REJECTED"):
        summary[state] = states.get(state, 0)
    unattributed = _unattributed(image, claimed)
    summary["unattributed_blocks"] = len(unattributed)
    summary["duplicate_blocks"] = sum(len(g["blocks"]) for g in duplicates)
    return {
        "case_id": case_id,
        "image_sha256": image.sha256,
        "ranker": ranker_name,
        "block_size": image.block_size,
        "block_count": image.block_count,
        "summary": summary,
        "artifacts": artifacts,
        "duplicates": duplicates,
        "unattributed": unattributed,
    }


def run_case(case_id: str, ranker_name: str = "baseline") -> str:
    """Analyse data/cases/<case>.img and write data/output/<case>.json. Returns the path."""
    report = analyze_image(os.path.join(CASES_DIR, f"{case_id}.img"), case_id, ranker_name)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{case_id}.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the VERDICT pipeline on one case.")
    parser.add_argument("--case", default="demo01")
    parser.add_argument("--ranker", default="baseline", choices=["baseline", "ml"])
    args = parser.parse_args()

    out_path = run_case(args.case, args.ranker)
    with open(out_path, "rb") as handle:
        raw = handle.read()
    report = json.loads(raw)
    print(f"case {report['case_id']}  image sha256 {report['image_sha256']}")
    print(f"summary: {report['summary']}")
    for art in report["artifacts"]:
        print(f"\n{art['id']}  anchor {art['anchor_block']:>4}  {art['state']:<8} "
              f"triage {art['triage']['score']}  assembly {art['assembly']}")
        print(f"  {art['explanation']}")
    print(f"\nwrote {os.path.relpath(out_path, ROOT)}  (sha256 {hashlib.sha256(raw).hexdigest()})")


if __name__ == "__main__":
    main()
