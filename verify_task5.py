"""Task-5 harness for evaluation/evaluate.py (CLAUDE.md §20 task 5).

Runs the evaluator on demo01 twice, checks that:
  1. The pipeline executes and returns a well-formed report.
  2. Every carved PNG anchor becomes an artifact_result entry.
  3. EXACT_RECOVERY is only set when the reconstructed SHA-256 matches the
     truth SHA-256, never merely on VALID status.
  4. FALSE_VERIFIED is detectable and (on demo01) is zero.
  5. Aggregate denominators equal the number of reconstructions attempted.
  6. Search failures are NOT counted as exact recoveries.
  7. The JSON report is written and matches the returned dict.
  8. Two runs on the same case produce byte-identical JSON (determinism).
  9. Truth is not read by any core/* module (grep-based check).
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from evaluation.evaluate import evaluate_case  # noqa: E402


CASE = "demo01"
IMAGE_PATH = os.path.join(ROOT, "data", "cases", f"{CASE}.img")
TRUTH_PATH = os.path.join(ROOT, "data", "truth", f"{CASE}.json")
OUT_DIR = os.path.join(ROOT, "data", "output")
OUT_JSON = os.path.join(OUT_DIR, "metrics.json")


def _write_report(report):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_JSON, "w") as handle:
        json.dump(report, handle, indent=2)


def _read_report(path):
    with open(path) as handle:
        return json.load(handle)


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("evaluator runs and returns a dict")
def _(state):
    report = evaluate_case(IMAGE_PATH, TRUTH_PATH)
    assert isinstance(report, dict) and "aggregate_metrics" in report
    state["report"] = report


@check("every carved anchor becomes an artifact_result")
def _(state):
    report = state["report"]
    found = report["anchor_metrics"]["found"]
    assert len(report["artifact_results"]) == len(found), \
        f"{len(report['artifact_results'])} results vs {len(found)} anchors"


@check("all found anchors match a truth artifact (no FP on demo01)")
def _(state):
    report = state["report"]
    assert report["anchor_metrics"]["false_positive_count"] == 0
    for entry in report["artifact_results"]:
        assert entry["artifact"] is not None, "found anchor has no truth mapping"


@check("EXACT_RECOVERY requires SHA-256 match with truth")
def _(state):
    with open(TRUTH_PATH) as handle:
        truth = json.load(handle)
    truth_by_block = {f["blocks"][0]: f for f in truth["files"] if f["type"] == "png"}
    for entry in state["report"]["artifact_results"]:
        block = entry["anchor"]["block"]
        recon_sha = entry["reconstruction"]["reconstructed_sha256"]
        expected_sha = truth_by_block[block]["sha256"] if block in truth_by_block else None
        if entry["exact_recovery"]:
            assert entry["reconstruction"]["status"] == "VERIFIED"
            assert recon_sha == expected_sha, \
                f"exact_recovery=True but sha mismatch: {recon_sha} vs {expected_sha}"


@check("FALSE_VERIFIED count is consistent (verified count = exact + false_verified)")
def _(state):
    ag = state["report"]["aggregate_metrics"]
    assert ag["verified_count"] == ag["exact_recovery"]["numerator"] + ag["false_verified_count"]


@check("demo01 has 0 false VERIFIED results")
def _(state):
    ag = state["report"]["aggregate_metrics"]
    assert ag["false_verified_count"] == 0, \
        f"unexpected false VERIFIED count: {ag['false_verified_count']}"


@check("aggregate denominators equal reconstructions_attempted")
def _(state):
    ag = state["report"]["aggregate_metrics"]
    n = ag["reconstructions_attempted"]
    assert ag["exact_recovery"]["denominator"] == n
    assert (ag["verified_count"]
            + ag["exhausted_candidates_count"]
            + ag["exhausted_budget_count"]
            + ag["rejected_anchor_count"]) == n


@check("search failures are not counted as exact recoveries")
def _(state):
    for entry in state["report"]["artifact_results"]:
        if entry["reconstruction"]["status"] != "VERIFIED":
            assert entry["exact_recovery"] is False, \
                f"{entry['artifact']}: non-VERIFIED counted as exact"


@check("known-intact PNGs denominator excludes damaged files")
def _(state):
    intact = state["report"]["aggregate_metrics"]["known_intact_bytes"]
    for entry in state["report"]["artifact_results"]:
        truth = entry["truth"]
        if truth is None:
            continue
        should_be_intact = (not truth["corrupted_blocks"]
                            and not truth["overwritten_blocks"])
        if entry["artifact"] in intact["artifacts"]:
            assert should_be_intact, f"{entry['artifact']} damaged yet listed as intact"
        else:
            if truth["blocks"][0] not in truth["overwritten_blocks"]:
                assert not should_be_intact, \
                    f"{entry['artifact']} intact but not listed"


@check("JSON report is written and equals the returned dict")
def _(state):
    _write_report(state["report"])
    assert os.path.exists(OUT_JSON)
    round_tripped = _read_report(OUT_JSON)
    assert round_tripped == state["report"], "on-disk report differs from returned dict"


@check("determinism: two runs on the same case produce identical dicts")
def _(state):
    report_a = evaluate_case(IMAGE_PATH, TRUTH_PATH)
    report_b = evaluate_case(IMAGE_PATH, TRUTH_PATH)
    assert report_a == report_b, "evaluator is not deterministic"


@check("determinism: written JSON is byte-identical across runs")
def _(state):
    report_a = evaluate_case(IMAGE_PATH, TRUTH_PATH)
    _write_report(report_a)
    with open(OUT_JSON, "rb") as h:
        bytes_a = h.read()
    report_b = evaluate_case(IMAGE_PATH, TRUTH_PATH)
    _write_report(report_b)
    with open(OUT_JSON, "rb") as h:
        bytes_b = h.read()
    assert bytes_a == bytes_b


@check("truth isolation: no data/truth reference in core/*")
def _(state):
    # Grep-based; matches the CLAUDE.md §12 hard rule exactly
    hits = []
    for name in os.listdir(os.path.join(ROOT, "core")):
        if not name.endswith(".py"):
            continue
        path = os.path.join(ROOT, "core", name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for i, line in enumerate(text.splitlines(), start=1):
            if re.search(r"data/truth|truth\.json", line):
                hits.append(f"{path}:{i}: {line.strip()}")
    assert not hits, "truth reference leaked into core/: " + "; ".join(hits)


def main():
    state = {}
    passed = failed = 0
    for name, fn in CHECKS:
        try:
            fn(state)
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
