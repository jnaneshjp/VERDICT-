"""Evaluation layer for VERDICT (CLAUDE.md §12, §13, §20 task 5).

This is the ONLY module allowed to consume data/truth/. It runs the real
production pipeline (ingest -> carve -> reconstruct) and then compares the
results against synthetic truth to compute:

  - PNG anchor precision/recall
  - per-artifact reconstruction outcome
  - EXACT_RECOVERY (VERIFIED and SHA-256 matches the truth original)
  - FALSE_VERIFIED (VERIFIED but SHA-256 differs from the truth original)
  - Baseline recovery on known-intact PNGs vs. all PNGs
  - Aggregate search-effort metrics (validator calls, backtracks, depth)

Never confuses "search failed" with "artifact physically unrecoverable"; both
are reported separately using the truth-side damage metadata.
"""
import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.carve import carve_png_anchors  # noqa: E402
from core.config import BUDGET  # noqa: E402
from core.ingest import ingest_image  # noqa: E402
import numpy as np  # noqa: E402

from core.pipeline import assembly_bytes, build_artifact  # noqa: E402
from core.rank import make_ranker  # noqa: E402
from core.search import ReconStatus, reconstruct_png  # noqa: E402
from generator.generate_case import CORPUS_IDS, build_corpus  # noqa: E402


# ---------------------------------------------------------------- helpers

def _damage_class(entry: Dict[str, Any]) -> str:
    """Categorise per-file truth damage."""
    if entry.get("corrupted_blocks"):
        return "byte_flip_corruption"
    status = entry["status"]
    if status == "overwritten":
        return "fully_overwritten"
    if status == "partially_overwritten":
        return "partially_overwritten"
    if status == "deleted":
        return "deleted_intact_bytes"          # bytes physically present
    return "intact"


def _bytes_still_present(entry: Dict[str, Any]) -> bool:
    """Truth says the file's original bytes are still on disk unchanged."""
    return not entry["corrupted_blocks"] and not entry["overwritten_blocks"]


def _anchor_expected(entry: Dict[str, Any]) -> bool:
    """Truth says the anchor block (blocks[0]) still holds the signature."""
    if not entry["blocks"]:
        return False
    return entry["blocks"][0] not in entry["overwritten_blocks"]


def _classify_reconstruction(recon_status: ReconStatus,
                             sha_match: Optional[bool]) -> str:
    """Human-facing category derived only from the production result + SHA check."""
    if recon_status is ReconStatus.VERIFIED:
        return "EXACT_RECOVERY" if sha_match else "FALSE_VERIFIED"
    return recon_status.value                  # EXHAUSTED_* / REJECTED_ANCHOR


def _original_files(truth: Dict[str, Any]) -> Dict[str, bytes]:
    """Rebuild every original file exactly as the generator made it (same corpus + seed).

    The generator builds the corpus first from default_rng([corpus_id, seed]), so
    this is byte-exact. Each rebuilt file is checked against the truth SHA-256.
    """
    rng = np.random.default_rng([CORPUS_IDS[truth["corpus"]], truth["seed"]])
    originals = {f["name"]: f["data"] for f in build_corpus(rng, truth["corpus"])}
    for entry in truth["files"]:
        if hashlib.sha256(originals[entry["name"]]).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"rebuilt {entry['name']} does not match its truth SHA-256")
    return originals


def _step_ranks(image, events, true_blocks: List[int]) -> List[Optional[int]]:
    """Rank of the true next block at each search step whose path so far is correct.

    Read from the search trail (CANDIDATE_TRIED events carry the ranked list).
    Blocks are compared by SHA-256 so a byte-identical copy counts as correct.
    None = the true block was not among the kept candidates.
    """
    true_shas = [image.blocks[b].sha256 for b in true_blocks]
    ranks: Dict[int, Optional[int]] = {}
    for e in events:
        if e.event != "CANDIDATE_TRIED":
            continue
        n = len(e.path) - 1                      # position being filled
        if n >= len(true_blocks) or n in ranks:
            continue
        if [image.blocks[b].sha256 for b in e.path[:n]] != true_shas[:n]:
            continue
        ranks[n] = next((i + 1 for i, c in enumerate(e.ranked)
                         if image.blocks[c].sha256 == true_shas[n]), None)
    return [ranks[n] for n in sorted(ranks)]


def _round(value: float, ndigits: int = 4) -> float:
    return round(value, ndigits)


def _ratio(numerator: int, denominator: int) -> Dict[str, Any]:
    ratio = _round(numerator / denominator, 4) if denominator else None
    return {"numerator": numerator, "denominator": denominator, "ratio": ratio}


# ------------------------------------------------------------- core

def evaluate_case(image_path: str, truth_path: str,
                  ranker_name: str = "baseline") -> Dict[str, Any]:
    """Run the production pipeline on `image_path` and score it against `truth_path`."""
    with open(truth_path) as handle:
        truth = json.load(handle)
    block_size = truth["block_size"]

    image = ingest_image(image_path, block_size=block_size)
    found_anchors = carve_png_anchors(image)
    originals = _original_files(truth)
    ranker = make_ranker(image, ranker_name)

    truth_pngs = [entry for entry in truth["files"] if entry["type"] == "png"]
    # Several truth files can start on the same block when a newer file was
    # written over an older one. An anchor belongs to the file whose first block
    # was NOT overwritten, i.e. the file whose bytes are actually there.
    truth_pngs_by_block: Dict[int, Dict[str, Any]] = {}
    for entry in truth_pngs:
        block = entry["blocks"][0]
        if block not in truth_pngs_by_block or _anchor_expected(entry):
            truth_pngs_by_block[block] = entry

    # ---- anchor comparison -------------------------------------------------
    expected_entries = [entry for entry in truth_pngs if _anchor_expected(entry)]
    expected_by_block = {entry["blocks"][0]: entry for entry in expected_entries}
    expected_set = {(entry["blocks"][0] * block_size, entry["blocks"][0])
                    for entry in expected_entries}
    found_set = {(anchor.byte_offset, anchor.block_index) for anchor in found_anchors}

    true_positives = sorted(expected_set & found_set)
    missed = sorted(expected_set - found_set)
    false_positives = sorted(found_set - expected_set)

    anchor_metrics = {
        "expected": [
            {"byte_offset": off, "block": blk,
             "artifact": expected_by_block[blk]["name"]}
            for off, blk in sorted(expected_set)
        ],
        "found": [
            {"byte_offset": off, "block": blk}
            for off, blk in sorted(found_set)
        ],
        "true_positive_count": len(true_positives),
        "missed": [
            {"byte_offset": off, "block": blk,
             "artifact": expected_by_block[blk]["name"]}
            for off, blk in missed
        ],
        "false_positive_count": len(false_positives),
        "false_positives": [
            {"byte_offset": off, "block": blk} for off, blk in false_positives
        ],
        "precision": _ratio(len(true_positives), len(found_set)),
        "recall": _ratio(len(true_positives), len(expected_set)),
    }

    # ---- per-artifact reconstruction --------------------------------------
    artifact_results: List[Dict[str, Any]] = []
    all_ranks: List[Optional[int]] = []
    for number, anchor in enumerate(found_anchors, start=1):
        result = reconstruct_png(image, anchor, ranker=ranker)
        truth_entry = truth_pngs_by_block.get(anchor.block_index)
        artifact = build_artifact(image, result, f"art_{number:03d}", anchor.signature)
        ranks: List[Optional[int]] = []
        if truth_entry and not truth_entry["overwritten_blocks"]:
            ranks = _step_ranks(image, result.events, truth_entry["blocks"])
        all_ranks.extend(ranks)
        prefix_match: Optional[bool] = None       # only judged for PARTIAL artifacts
        if artifact["state"] == "PARTIAL":
            verified = assembly_bytes(image, artifact["assembly"])[:artifact["verified_bytes"]]
            original = originals.get(truth_entry["name"], b"") if truth_entry else b""
            prefix_match = verified == original[:len(verified)]

        sha_match: Optional[bool]
        if truth_entry and result.status is ReconStatus.VERIFIED and result.reconstructed_sha256:
            sha_match = (result.reconstructed_sha256 == truth_entry["sha256"])
        else:
            sha_match = None if result.status is not ReconStatus.VERIFIED else False

        classification = _classify_reconstruction(result.status, sha_match)

        artifact_entry: Dict[str, Any] = {
            "anchor": {
                "byte_offset": anchor.byte_offset,
                "block": anchor.block_index,
            },
            "artifact": truth_entry["name"] if truth_entry else None,
            "truth": None if truth_entry is None else {
                "size": truth_entry["size"],
                "sha256": truth_entry["sha256"],
                "blocks": truth_entry["blocks"],
                "status": truth_entry["status"],
                "corrupted_blocks": truth_entry["corrupted_blocks"],
                "overwritten_blocks": truth_entry["overwritten_blocks"],
                "damage_class": _damage_class(truth_entry),
                "bytes_still_present": _bytes_still_present(truth_entry),
            },
            "reconstruction": {
                "status": result.status.value,
                "reason": result.reason,
                "reconstructed_length": result.reconstructed_length,
                "reconstructed_sha256": result.reconstructed_sha256,
                "path": result.path,
                "search_path": result.search_path,
                "verified_upto": result.verified_upto,
                "validation_count": result.validation_count,
                "backtrack_count": result.backtrack_count,
                "branches_tried": result.branches_tried,
                "max_depth": result.max_depth,
                "final_validator_status":
                    result.final_validator.status.value
                    if result.final_validator is not None else None,
                "final_validator_reason":
                    result.final_validator.reason_code
                    if result.final_validator is not None else None,
            },
            "state": artifact["state"],
            "verified_bytes": artifact["verified_bytes"],
            "step_ranks": ranks,
            "partial_prefix_matches_original": prefix_match,
            "sha_match": sha_match,
            "classification": classification,
            "exact_recovery": bool(sha_match) if sha_match is not None else False,
        }
        artifact_results.append(artifact_entry)

    artifact_results.sort(key=lambda r: r["anchor"]["byte_offset"])

    # ---- aggregate metrics -------------------------------------------------
    denom = len(artifact_results)
    verified = [r for r in artifact_results if r["reconstruction"]["status"] == "VERIFIED"]
    exact = [r for r in artifact_results if r["exact_recovery"]]
    false_verified = [r for r in verified if not r["exact_recovery"]]
    exh_cand = [r for r in artifact_results if r["reconstruction"]["status"] == "EXHAUSTED_CANDIDATES"]
    exh_bud = [r for r in artifact_results if r["reconstruction"]["status"] == "EXHAUSTED_BUDGET"]
    rejected = [r for r in artifact_results if r["reconstruction"]["status"] == "REJECTED_ANCHOR"]

    total_valcalls = sum(r["reconstruction"]["validation_count"] for r in artifact_results)
    total_backtracks = sum(r["reconstruction"]["backtrack_count"] for r in artifact_results)
    depths = [r["reconstruction"]["max_depth"] for r in artifact_results]

    intact_pngs = [entry for entry in truth_pngs
                   if _bytes_still_present(entry) and _anchor_expected(entry)]
    intact_names = {entry["name"] for entry in intact_pngs}
    intact_results = [r for r in artifact_results if r["artifact"] in intact_names]
    intact_exact = [r for r in intact_results if r["exact_recovery"]]

    aggregate = {
        "state_counts": {state: sum(1 for r in artifact_results if r["state"] == state)
                         for state in ("PROVEN", "PLAUSIBLE", "PARTIAL", "REJECTED")},
        "attempts_per_artifact": _round(total_valcalls / denom) if denom else None,
        "partial_prefix_match": _ratio(
            sum(1 for r in artifact_results if r["partial_prefix_matches_original"] is True),
            sum(1 for r in artifact_results if r["state"] == "PARTIAL")),
        "top1": _ratio(sum(1 for r in all_ranks if r == 1), len(all_ranks)),
        "top5": _ratio(sum(1 for r in all_ranks if r is not None and r <= 5), len(all_ranks)),
        "png_artifacts_in_truth": len(truth_pngs),
        "png_anchors_expected": len(expected_set),
        "png_anchors_found": len(found_set),
        "reconstructions_attempted": denom,
        "verified_count": len(verified),
        "exact_recovery": _ratio(len(exact), denom),
        "false_verified_count": len(false_verified),
        "exhausted_candidates_count": len(exh_cand),
        "exhausted_budget_count": len(exh_bud),
        "rejected_anchor_count": len(rejected),
        "total_validator_calls": total_valcalls,
        "avg_validator_calls_per_anchor":
            _round(total_valcalls / denom) if denom else None,
        "avg_backtracks_per_anchor":
            _round(total_backtracks / denom) if denom else None,
        "max_backtracks": max(
            (r["reconstruction"]["backtrack_count"] for r in artifact_results),
            default=0),
        "avg_search_depth": _round(sum(depths) / len(depths)) if depths else None,
        "max_search_depth": max(depths, default=0),
        "known_intact_bytes": {
            "denominator": len(intact_pngs),
            "artifacts": sorted(entry["name"] for entry in intact_pngs),
            "baseline_recovered": _ratio(len(intact_exact), len(intact_pngs)),
        },
    }

    limitations = [
        "Baseline reconstruction uses a fixed physical-locality ranker with "
        f"top_k=8 and validation budget={BUDGET}; known-intact artifacts whose "
        "correct successor block sits outside that window (e.g. a large "
        "physical gap in a fragmented PNG) are not recovered by the current "
        "search strategy and are NOT evidence that the artifact is physically "
        "unrecoverable.",
        "P0 evaluates PNG artifacts only; logs/CSVs from the truth are listed "
        "but not scored for reconstruction.",
        "'Byte-flip corruption' failures are physical-damage cases: the "
        "validator's CRC check correctly rejects them and no search strategy "
        "over intact blocks can recover altered bytes.",
    ]

    return {
        "case_id": truth.get("case_id"),
        "image_sha256": image.sha256,
        "block_size": block_size,
        "block_count": image.block_count,
        "search_config": {
            "budget": BUDGET,
            "ranker": ranker_name,
        },
        "anchor_metrics": anchor_metrics,
        "artifact_results": artifact_results,
        "aggregate_metrics": aggregate,
        "limitations": limitations,
    }


# --------------------------------------------------------- human report

def _print_report(report: Dict[str, Any]) -> None:
    am = report["anchor_metrics"]
    ag = report["aggregate_metrics"]
    print(f"[evaluation of {report['case_id']}]")
    print(f"image sha256:      {report['image_sha256']}")
    print(f"block_size={report['block_size']}  block_count={report['block_count']}")
    print(f"search config:     {report['search_config']}")

    print(f"\nAnchor metrics:")
    print(f"  expected={len(am['expected'])}  found={len(am['found'])}"
          f"  TP={am['true_positive_count']}"
          f"  missed={len(am['missed'])}  FP={am['false_positive_count']}")
    print(f"  precision={_fmt_ratio(am['precision'])}"
          f"  recall={_fmt_ratio(am['recall'])}")

    print(f"\nPer-artifact results:")
    header = ("artifact", "anchor", "damage", "recon status",
              "exact", "vlds", "bt", "recon sha")
    print(f"  {'artifact':<24} {'blk':>4}  {'damage':<24} "
          f"{'recon':<22} {'exact':<6} {'vlds':>4} {'bt':>3}  recon_sha")
    for r in report["artifact_results"]:
        truth = r["truth"] or {}
        exact = ("yes" if r["exact_recovery"]
                 else "FALSE" if r["classification"] == "FALSE_VERIFIED"
                 else "no")
        recon_sha = (r["reconstruction"]["reconstructed_sha256"] or "-")[:12]
        print(f"  {(r['artifact'] or '?'):<24} "
              f"{r['anchor']['block']:>4}  "
              f"{truth.get('damage_class', '?'):<24} "
              f"{r['reconstruction']['status']:<22} "
              f"{exact:<6} "
              f"{r['reconstruction']['validation_count']:>4} "
              f"{r['reconstruction']['backtrack_count']:>3}  "
              f"{recon_sha}")

    print(f"\nAggregate metrics:")
    print(f"  exact recovery:              {_fmt_ratio(ag['exact_recovery'])}")
    print(f"  verified (format-valid):     {ag['verified_count']} / {ag['reconstructions_attempted']}")
    print(f"  false VERIFIED:              {ag['false_verified_count']} / {ag['reconstructions_attempted']}")
    print(f"  exhausted (candidates):      {ag['exhausted_candidates_count']} / {ag['reconstructions_attempted']}")
    print(f"  exhausted (budget):          {ag['exhausted_budget_count']} / {ag['reconstructions_attempted']}")
    print(f"  rejected anchor:             {ag['rejected_anchor_count']} / {ag['reconstructions_attempted']}")

    print(f"\nSearch effort:")
    print(f"  total validator calls:       {ag['total_validator_calls']}")
    print(f"  avg per anchor:              {ag['avg_validator_calls_per_anchor']}")
    print(f"  avg backtracks per anchor:   {ag['avg_backtracks_per_anchor']}")
    print(f"  max backtracks:              {ag['max_backtracks']}")
    print(f"  avg search depth:            {ag['avg_search_depth']}")
    print(f"  max search depth:            {ag['max_search_depth']}")

    intact = ag["known_intact_bytes"]
    print(f"\nKnown-intact PNGs (per truth):")
    print(f"  denominator={intact['denominator']}  artifacts={intact['artifacts']}")
    print(f"  baseline recovered:          {_fmt_ratio(intact['baseline_recovered'])}")

    print(f"\nLimitations:")
    for line in report["limitations"]:
        print(f"  - {line}")


def _fmt_ratio(r: Dict[str, Any]) -> str:
    if r["ratio"] is None:
        return f"{r['numerator']}/{r['denominator']}"
    return f"{r['numerator']}/{r['denominator']} = {r['ratio'] * 100:.1f}%"


# --------------------------------------------------------- totals across cases

def _totals(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum numerators and denominators over cases (never average ratios)."""
    def total(key):
        num = sum(r["aggregate_metrics"][key]["numerator"] for r in reports)
        den = sum(r["aggregate_metrics"][key]["denominator"] for r in reports)
        return _ratio(num, den)
    artifacts = sum(r["aggregate_metrics"]["reconstructions_attempted"] for r in reports)
    attempts = sum(r["aggregate_metrics"]["total_validator_calls"] for r in reports)
    states = {}
    for state in ("PROVEN", "PLAUSIBLE", "PARTIAL", "REJECTED"):
        states[state] = sum(r["aggregate_metrics"]["state_counts"][state] for r in reports)
    intact_num = sum(r["aggregate_metrics"]["known_intact_bytes"]["baseline_recovered"]["numerator"]
                     for r in reports)
    intact_den = sum(r["aggregate_metrics"]["known_intact_bytes"]["denominator"] for r in reports)
    return {
        "cases": len(reports),
        "artifacts": artifacts,
        "state_counts": states,
        "exact_recovery": total("exact_recovery"),
        "false_verified_count": sum(r["aggregate_metrics"]["false_verified_count"] for r in reports),
        "known_intact_recovered": _ratio(intact_num, intact_den),
        "total_attempts": attempts,
        "attempts_per_artifact": _round(attempts / artifacts) if artifacts else None,
        "partial_prefix_match": total("partial_prefix_match"),
        "top1": total("top1"),
        "top5": total("top5"),
    }


def _print_table(results: Dict[str, Dict[str, Dict[str, Any]]], cases: List[str]) -> None:
    rankers = list(results)
    head = "".join(f" | {name:<9} {'att':>4}" for name in rankers)
    print(f"{'case':<7} {'blk':>4}  {'truth file':<22} {'truth status':<22}{head}")
    for case in cases:
        rows = zip(*(results[name][case]["artifact_results"] for name in rankers))
        for per_ranker in rows:
            first = per_ranker[0]
            truth = first["truth"] or {}
            cells = "".join(f" | {r['state']:<9} {r['reconstruction']['validation_count']:>4}"
                            for r in per_ranker)
            print(f"{case:<7} {first['anchor']['block']:>4}  {(first['artifact'] or '?'):<22} "
                  f"{truth.get('damage_class', '?'):<22}{cells}")


# --------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the pipeline against truth.")
    parser.add_argument("--case", nargs="+", default=["demo01"])
    parser.add_argument("--ranker", nargs="+", default=["baseline"], choices=["baseline", "ml"])
    parser.add_argument("--output",
                        default=os.path.join(ROOT, "data", "output", "metrics.json"),
                        help="Where to write the metrics JSON (served verbatim by the API).")
    parser.add_argument("--no-write", action="store_true",
                        help="Do not write the JSON report to disk.")
    args = parser.parse_args()

    results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for name in args.ranker:
        results[name] = {}
        for case in args.case:
            image_path = os.path.join(ROOT, "data", "cases", f"{case}.img")
            truth_path = os.path.join(ROOT, "data", "truth", f"{case}.json")
            results[name][case] = evaluate_case(image_path, truth_path, name)

    if len(args.case) == 1 and len(args.ranker) == 1:
        _print_report(results[args.ranker[0]][args.case[0]])
    _print_table(results, args.case)
    metrics = {"cases": args.case, "rankers": {}}
    for name in args.ranker:
        totals = _totals(list(results[name].values()))
        metrics["rankers"][name] = {"totals": totals, "per_case": results[name]}
        print(f"\n[{name}] {json.dumps(totals)}")

    if not args.no_write:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, "w") as handle:
            json.dump(metrics, handle, indent=2)
        print(f"\nmetrics written to {os.path.relpath(args.output, ROOT)}")


if __name__ == "__main__":
    main()
