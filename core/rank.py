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
from ml import features_docx
from ml.features import features_for

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "ml", "models", "ranker.joblib")
DOCX_MODEL_PATH = os.path.join(os.path.dirname(MODEL_PATH), "ranker_docx.joblib")


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


class DocxMLRanker(Ranker):
    """Separate model for ZIP / DOCX anchors (trained on docx_train, chosen on docx_val)."""

    def __init__(self, image: Image, model_path: str = DOCX_MODEL_PATH):
        self.image = image
        self.model = joblib.load(model_path)["model"]
        self.baseline = BaselineRanker(image)

    def order(self, path: List[int], candidates: List[int]) -> List[int]:
        if not candidates:
            return []
        scores = self.model.predict_proba(features_docx.features_for(self.image, path, candidates))[:, 1]
        score_of = dict(zip(candidates, scores))
        tiebreak = {c: i for i, c in enumerate(self.baseline.order(path, candidates))}
        return sorted(candidates, key=lambda c: (-score_of[c], tiebreak[c]))


def make_zip_ranker(image: Image, name: str):
    """Ranker for ZIP / DOCX anchors and the name of the one actually used.

    'ml' uses the DOCX model if ml/models/ranker_docx.joblib exists; otherwise
    it falls back to the baseline and says so ('baseline').
    """
    if name == "ml" and os.path.exists(DOCX_MODEL_PATH):
        return DocxMLRanker(image), "ml"
    return BaselineRanker(image), "baseline"


def make_ranker(image: Image, name: str) -> Ranker:
    """'baseline' or 'ml'."""
    if name == "baseline":
        return BaselineRanker(image)
    if name == "ml":
        return MLRanker(image)
    raise ValueError(f"unknown ranker '{name}'")
