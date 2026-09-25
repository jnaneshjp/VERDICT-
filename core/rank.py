"""Candidate rankers (CLAUDE.md §10).

BaselineRanker lives in core/search.py; MLRanker uses the same Ranker
interface, so the search does not know which one it is using.

MLRanker scores every candidate in the window with the trained
GradientBoostingClassifier and sorts by that score (ties -> baseline order).
The search then keeps the first TOP_K. The score is only used for ORDER: it is
never shown as a probability, and the PNG validator still decides correctness.
"""
import os
from typing import List

import joblib

from core.ingest import Image
from core.search import BaselineRanker, Ranker
from ml.features import features_for

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "ml", "models", "ranker.joblib")


class MLRanker(Ranker):
    def __init__(self, image: Image, model_path: str = MODEL_PATH):
        self.image = image
        self.model = joblib.load(model_path)["model"]
        self.baseline = BaselineRanker(image)

    def order(self, path: List[int], candidates: List[int]) -> List[int]:
        if not candidates:
            return []
        scores = self.model.predict_proba(features_for(self.image, path, candidates))[:, 1]
        score_of = dict(zip(candidates, scores))
        tiebreak = {c: i for i, c in enumerate(self.baseline.order(path, candidates))}
        return sorted(candidates, key=lambda c: (-score_of[c], tiebreak[c]))


def make_ranker(image: Image, name: str) -> Ranker:
    """'baseline' or 'ml'."""
    if name == "baseline":
        return BaselineRanker(image)
    if name == "ml":
        return MLRanker(image)
    raise ValueError(f"unknown ranker '{name}'")
