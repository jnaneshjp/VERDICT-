"""Train the candidate ranker (CLAUDE.md §10).

Reads truth for TRAIN and VAL cases only (never demo). For every intact PNG,
replays the search along its true block path: at each step it builds the
candidate set exactly as the search does, labels the true next block (or any
byte-identical copy) positive and every other candidate negative.

Model selection: every setting in GRID is trained on train and scored on val
by how often the true next block is ranked 1st. The best model is saved to
ml/models/ranker.joblib together with its val scores.

Usage: python ml/train.py
"""
import json
import os
import sys
from typing import Dict, List, Tuple

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.config import TOP_K, WINDOW  # noqa: E402
from core.ingest import ingest_image  # noqa: E402
from core.search import BaselineRanker, _candidates, _dedup_by_sha  # noqa: E402
from ml.features import FEATURE_NAMES, features_for  # noqa: E402

TRAIN_CASES = [f"train0{s}" for s in range(1, 9)]
VAL_CASES = ["val101", "val102", "val103"]
MODEL_PATH = os.path.join(ROOT, "ml", "models", "ranker.joblib")
GRID = [{"n_estimators": n, "max_depth": d, "learning_rate": 0.1}
        for n in (100, 200) for d in (2, 3)]
SEED = 0


def _load(case_id: str):
    image = ingest_image(os.path.join(ROOT, "data", "cases", f"{case_id}.img"))
    with open(os.path.join(ROOT, "data", "truth", f"{case_id}.json")) as handle:
        truth = json.load(handle)
    return image, truth


def _usable_pngs(truth: dict) -> List[dict]:
    """PNGs whose blocks were never overwritten, so the true order is on disk."""
    return [f for f in truth["files"]
            if f["type"] == "png" and not f["overwritten_blocks"] and len(f["blocks"]) > 1]


def replay_steps(image, blocks: List[int]):
    """Yield (path, candidates, positive_mask) for each step along the true path."""
    for i in range(len(blocks) - 1):
        path = blocks[:i + 1]
        cands = _dedup_by_sha(image, _candidates(image, set(path), path[-1], WINDOW))
        true_sha = image.blocks[blocks[i + 1]].sha256
        mask = np.array([image.blocks[c].sha256 == true_sha for c in cands])
        yield path, cands, mask


def build_dataset(case_ids: List[str]) -> Tuple[np.ndarray, np.ndarray, List[Tuple]]:
    """Rows for every candidate at every step. Also returns per-step groups for scoring."""
    xs, ys, groups = [], [], []
    for case_id in case_ids:
        image, truth = _load(case_id)
        for entry in _usable_pngs(truth):
            for path, cands, mask in replay_steps(image, entry["blocks"]):
                groups.append((image, path, cands, mask))
                if not mask.any():
                    continue                      # true block outside the window: nothing to learn
                xs.append(features_for(image, path, cands))
                ys.append(mask.astype(int))
    return np.vstack(xs), np.concatenate(ys), groups


def _rank_of_true(order: List[int], cands: List[int], mask: np.ndarray) -> int:
    positives = {c for c, m in zip(cands, mask) if m}
    for rank, c in enumerate(order, start=1):
        if c in positives:
            return rank
    return len(order) + 1


def score_steps(groups, order_fn) -> Dict[str, float]:
    """Top-1 / top-TOP_K rate of the true next block over all replayed steps."""
    ranks = [_rank_of_true(order_fn(image, path, cands), cands, mask)
             for image, path, cands, mask in groups]
    n = len(ranks)
    return {"steps": n,
            "top1": sum(r == 1 for r in ranks) / n,
            f"top{TOP_K}": sum(r <= TOP_K for r in ranks) / n,
            "mean_rank": float(np.mean(ranks))}


def _model_order(model):
    def order(image, path, cands):
        probs = dict(zip(cands, model.predict_proba(features_for(image, path, cands))[:, 1]))
        baseline = BaselineRanker(image).order(path, cands)
        tiebreak = {c: i for i, c in enumerate(baseline)}
        return sorted(cands, key=lambda c: (-probs[c], tiebreak[c]))
    return order


def main() -> None:
    x_train, y_train, _ = build_dataset(TRAIN_CASES)
    _, _, val_groups = build_dataset(VAL_CASES)
    print(f"train rows {len(y_train)} (positives {int(y_train.sum())}), "
          f"val steps {len(val_groups)}")

    baseline_val = score_steps(val_groups, lambda im, p, c: BaselineRanker(im).order(p, c))
    print(f"val baseline: {baseline_val}")

    best = None
    for params in GRID:
        model = GradientBoostingClassifier(random_state=SEED, **params).fit(x_train, y_train)
        val = score_steps(val_groups, _model_order(model))
        print(f"val ml {params}: {val}")
        if best is None or (val["top1"], -val["mean_rank"]) > (best[2]["top1"], -best[2]["mean_rank"]):
            best = (model, params, val)

    model, params, val = best
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "params": params, "features": FEATURE_NAMES,
                 "val": val, "val_baseline": baseline_val,
                 "train_cases": TRAIN_CASES, "val_cases": VAL_CASES}, MODEL_PATH)
    importances = dict(zip(FEATURE_NAMES, np.round(model.feature_importances_, 3)))
    print(f"chosen {params}\nfeature importances {importances}\nsaved {os.path.relpath(MODEL_PATH, ROOT)}")


if __name__ == "__main__":
    main()
