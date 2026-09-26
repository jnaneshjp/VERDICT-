"""Train the DOCX / ZIP candidate ranker (P1 prototype).

Separate from the PNG ranker: its own disks, features and model file.
Reads truth for docx_train and docx_val disks only (never docx01-docx10).
For every DOCX whose blocks were never overwritten, replays the search along
its true block path, exactly like ml/train.py does for PNG.

Model selection: each GRID setting is trained on docx_train and scored on
docx_val by how often the true next block is ranked 1st. The best model is
saved to ml/models/ranker_docx.joblib. ml/models/ranker.joblib is not touched.

Usage: python ml/train_docx.py
"""
import os
import sys
from typing import List

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.search import BaselineRanker  # noqa: E402
from ml.features_docx import FEATURE_NAMES, features_for  # noqa: E402
from ml.train import GRID, SEED, _load, replay_steps, score_steps  # noqa: E402

TRAIN_CASES = [f"docxtrain{s}" for s in range(301, 309)]
VAL_CASES = [f"docxval{s}" for s in range(401, 404)]
MODEL_PATH = os.path.join(ROOT, "ml", "models", "ranker_docx.joblib")


def _usable_docx(truth: dict) -> List[dict]:
    return [f for f in truth["files"]
            if f["type"] == "docx" and not f["overwritten_blocks"] and len(f["blocks"]) > 1]


def build_dataset(case_ids: List[str]):
    xs, ys, groups = [], [], []
    for case_id in case_ids:
        image, truth = _load(case_id)
        for entry in _usable_docx(truth):
            for path, cands, mask in replay_steps(image, entry["blocks"]):
                groups.append((image, path, cands, mask))
                if not mask.any():
                    continue
                xs.append(features_for(image, path, cands))
                ys.append(mask.astype(int))
    return np.vstack(xs), np.concatenate(ys), groups


def _model_order(model):
    def order(image, path, cands):
        probs = dict(zip(cands, model.predict_proba(features_for(image, path, cands))[:, 1]))
        tiebreak = {c: i for i, c in enumerate(BaselineRanker(image).order(path, cands))}
        return sorted(cands, key=lambda c: (-probs[c], tiebreak[c]))
    return order


def main() -> None:
    x_train, y_train, train_groups = build_dataset(TRAIN_CASES)
    _, _, val_groups = build_dataset(VAL_CASES)
    print(f"train rows {len(y_train)} (positives {int(y_train.sum())}, steps {len(train_groups)}), "
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
    joblib.dump({"model": model, "params": params, "features": FEATURE_NAMES,
                 "val": val, "val_baseline": baseline_val,
                 "train_cases": TRAIN_CASES, "val_cases": VAL_CASES}, MODEL_PATH)
    importances = dict(zip(FEATURE_NAMES, np.round(model.feature_importances_, 3)))
    print(f"chosen {params}\nfeature importances {importances}\nsaved {os.path.relpath(MODEL_PATH, ROOT)}")


if __name__ == "__main__":
    main()
