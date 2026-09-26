"""Evaluate the ZIP / DOCX prototype against truth (P1). Baseline ranker only.

Runs the production pipeline on each docx case, keeps its ZIP artifacts, and
scores them against the generator's truth. Like evaluate.py, this is allowed to
read data/truth/; core/ and api/ are not.

Metrics (numerator / denominator):
  exact_recovery    PROVEN and SHA-256 equals the matched original / intact DOCX files
  false_proven      PROVEN but SHA-256 differs from the matched original
  proven_matching_an_original_file
                    PROVEN whose SHA-256 equals ANY original on that disk (no anchor matching)
  partial_prefix_match
                    PARTIAL whose verified bytes equal the same-length start of the original

Usage: python evaluation/evaluate_docx.py            (docx01-docx10)
Writes data/output/metrics_docx.json. data/output/metrics.json is not touched.
"""
import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.ingest import ingest_image  # noqa: E402
from core.pipeline import CASES_DIR, analyze_image, assembly_bytes  # noqa: E402
from evaluation.evaluate import (_anchor_expected, _bytes_still_present,  # noqa: E402
                                 _damage_class, _original_files, _ratio, _round)

DEFAULT_CASES = [f"docx{n:02d}" for n in range(1, 11)]
OUTPUT = os.path.join(ROOT, "data", "output", "metrics_docx.json")


def evaluate_case(case: str) -> dict:
    with open(os.path.join(ROOT, "data", "truth", f"{case}.json")) as handle:
        truth = json.load(handle)
    originals = _original_files(truth)               # checked against truth SHA-256
    all_shas = {entry["sha256"] for entry in truth["files"]}
    docs = [entry for entry in truth["files"] if entry["type"] == "docx"]
    by_block = {}
    for entry in docs:                               # prefer the file whose first block survived
        block = entry["blocks"][0]
        if block not in by_block or _anchor_expected(entry):
            by_block[block] = entry
    intact = [entry for entry in docs if _bytes_still_present(entry)]

    image_path = os.path.join(CASES_DIR, f"{case}.img")
    image = ingest_image(image_path)
    report = analyze_image(image_path, case, "baseline")
    zip_artifacts = [a for a in report["artifacts"] if a["type"] in ("docx", "zip")]
    rows = []
    for art in zip_artifacts:
        entry = by_block.get(art["anchor_block"])
        original = originals.get(entry["name"], b"") if entry else b""
        row = {"anchor_block": art["anchor_block"], "state": art["state"], "type": art["type"],
               "verified_bytes": art["verified_bytes"], "attempts": art["attempts"],
               "sha256": art["sha256"], "truth_file": entry["name"] if entry else None,
               "truth_sha256": entry["sha256"] if entry else None,
               "truth_size": len(original) if entry else None,
               "damage_class": _damage_class(entry) if entry else None,
               "exact": False, "false_proven": False, "prefix_match": None,
               "matches_an_original": None}
        if art["state"] == "PROVEN":
            row["exact"] = entry is not None and art["sha256"] == entry["sha256"]
            row["false_proven"] = not row["exact"]
            row["matches_an_original"] = art["sha256"] in all_shas
        elif art["state"] == "PARTIAL":
            prefix = assembly_bytes(image, art["assembly"])[:art["verified_bytes"]]
            row["prefix_match"] = entry is not None and prefix == original[:len(prefix)]
        rows.append(row)
    return {"case_id": case, "corpus": truth["corpus"], "seed": truth["seed"],
            "image_sha256": report["image_sha256"],
            "intact_docx_files": sorted(e["name"] for e in intact),
            "artifacts": rows}


def totals(cases: list) -> dict:
    rows = [row for case in cases for row in case["artifacts"]]
    proven = [r for r in rows if r["state"] == "PROVEN"]
    partial = [r for r in rows if r["state"] == "PARTIAL"]
    intact_count = sum(len(case["intact_docx_files"]) for case in cases)
    return {
        "cases": len(cases),
        "ranker": "baseline",
        "artifacts": len(rows),
        "state_counts": {s: sum(r["state"] == s for r in rows)
                         for s in ("PROVEN", "PLAUSIBLE", "PARTIAL", "REJECTED")},
        "types": dict(Counter(r["type"] for r in rows)),
        "exact_recovery": _ratio(sum(r["exact"] for r in rows), intact_count),
        "false_proven": _ratio(sum(r["false_proven"] for r in proven), len(proven)),
        "proven_matching_an_original_file": _ratio(sum(bool(r["matches_an_original"]) for r in proven),
                                                   len(proven)),
        "partial_prefix_match": _ratio(sum(bool(r["prefix_match"]) for r in partial), len(partial)),
        "attempts_per_artifact": _round(sum(r["attempts"] for r in rows) / len(rows)) if rows else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the DOCX prototype against truth.")
    parser.add_argument("--case", nargs="+", default=DEFAULT_CASES)
    args = parser.parse_args()
    cases = [evaluate_case(case) for case in args.case]
    for case in cases:
        for r in case["artifacts"]:
            print(f"{case['case_id']}  blk {r['anchor_block']:>3}  {r['state']:<8} {r['type']:<5} "
                  f"{(r['truth_file'] or '?'):<38} {r['damage_class'] or '?':<22} "
                  f"exact={r['exact']} prefix_ok={r['prefix_match']} attempts={r['attempts']}")
    result = {"cases": args.case, "totals": totals(cases), "per_case": cases}
    print(json.dumps(result["totals"], indent=1))
    with open(OUTPUT, "w") as handle:
        json.dump(result, handle, indent=2)
    print(f"metrics written to {os.path.relpath(OUTPUT, ROOT)}")


if __name__ == "__main__":
    main()
