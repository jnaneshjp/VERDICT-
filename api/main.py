"""VERDICT HTTP API (CLAUDE.md §15). Four routes, JSON files on disk, no database.

Run: uvicorn api.main:app --reload --port 8000
"""
import json
import os
import re
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from core.ingest import Image, ingest_image
from core.pipeline import CASES_DIR, OUTPUT_DIR, assembly_bytes, output_path, run_case
from core.preview import preview_png

RANKERS = ("baseline", "ml")
CASE_ID = re.compile(r"^[A-Za-z0-9_-]+$")    # keeps case ids from reaching outside data/

app = FastAPI(title="VERDICT")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                   allow_methods=["GET", "POST"], allow_headers=["*"],
                   expose_headers=["X-Verdict-Preview", "X-Verdict-Rows", "X-Verdict-Height"])


class AnalyzeRequest(BaseModel):
    case_id: str
    ranker: str = "baseline"


def _check(case_id: str, ranker: str) -> None:
    if not CASE_ID.match(case_id):
        raise HTTPException(400, "invalid case_id")
    if ranker not in RANKERS:
        raise HTTPException(400, f"ranker must be one of {list(RANKERS)}")


def _load_case(case_id: str, ranker: str) -> dict:
    _check(case_id, ranker)
    path = output_path(case_id, ranker)
    if not os.path.exists(path):
        raise HTTPException(404, f"no analysis for {case_id} with ranker {ranker}; POST /case/analyze first")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=4)            # each image is ~8 MB; keep at most 4 in memory
def _image(case_id: str) -> Image:
    """Ingested storage image, kept in memory so previews are fast."""
    return ingest_image(os.path.join(CASES_DIR, f"{case_id}.img"))


@app.post("/case/analyze")
def analyze(request: AnalyzeRequest) -> dict:
    _check(request.case_id, request.ranker)
    if not os.path.exists(os.path.join(CASES_DIR, f"{request.case_id}.img")):
        raise HTTPException(404, f"no image data/cases/{request.case_id}.img")
    run_case(request.case_id, request.ranker)
    report = _load_case(request.case_id, request.ranker)
    return {"case_id": report["case_id"], "ranker": report["ranker"],
            "image_sha256": report["image_sha256"], "summary": report["summary"]}


@app.get("/case/{case_id}")
def get_case(case_id: str, ranker: str = Query("baseline")) -> FileResponse:
    _load_case(case_id, ranker)                    # validates and 404s if missing
    return FileResponse(output_path(case_id, ranker), media_type="application/json")


@app.get("/artifact/{case_id}/{artifact_id}/preview")
def preview(case_id: str, artifact_id: str, ranker: str = Query("baseline")) -> Response:
    report = _load_case(case_id, ranker)
    artifact = next((a for a in report["artifacts"] if a["id"] == artifact_id), None)
    if artifact is None:
        raise HTTPException(404, f"no artifact {artifact_id} in {case_id}")
    data = assembly_bytes(_image(case_id), artifact["assembly"])[:artifact["verified_bytes"]]
    rendered = preview_png(artifact["state"], data)
    if rendered is None:
        raise HTTPException(404, f"no preview for a {artifact['state']} artifact")
    png, info = rendered
    return Response(png, media_type="image/png", headers={
        "X-Verdict-Preview": info["note"],
        "X-Verdict-Rows": str(info["rows"]),
        "X-Verdict-Height": str(info["height"]),
    })


@app.get("/metrics")
def metrics() -> FileResponse:
    path = os.path.join(OUTPUT_DIR, "metrics.json")
    if not os.path.exists(path):
        raise HTTPException(404, "metrics.json not found; run evaluation/evaluate.py")
    return FileResponse(path, media_type="application/json")
