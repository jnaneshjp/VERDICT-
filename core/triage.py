"""Triage score for VERDICT (CLAUDE.md §9).

A transparent weighted heuristic that helps decide what to look at first.
It is never a conclusion: every component is returned alongside the score so
the UI can show exactly how the number was built.
"""
from core.config import CATEGORY_VALUE, STATE_VALUE, W_CATEGORY, W_COMPLETE, W_STATE

TRIAGE_LABEL = "Triage assistance — not a forensic conclusion."


def triage(state: str, completeness: float, category: str) -> dict:
    """score = w_state * state_value + w_complete * completeness + w_cat * category_value"""
    components = {
        "state": STATE_VALUE[state],
        "completeness": round(completeness, 4),
        "category": CATEGORY_VALUE.get(category, 0.0),
    }
    score = (W_STATE * components["state"]
             + W_COMPLETE * components["completeness"]
             + W_CATEGORY * components["category"])
    return {
        "score": round(score, 4),
        "components": components,
        "weights": {"state": W_STATE, "completeness": W_COMPLETE, "category": W_CATEGORY},
        "label": TRIAGE_LABEL,
    }
