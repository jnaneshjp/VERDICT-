# CLAUDE.md — VERDICT

This is the source of truth for implementing VERDICT. **Do not redesign.** If something here looks wrong or blocks you, stop and ask. Do not improvise an alternative.

---

## 1. Problem

CalmStacks 2026, 24-hour hackathon, **offline venue**.
Challenge: **AI-Assisted Intelligent Data Recovery and Digital Evidence Reconstruction.**

Identify, reconstruct, classify and prioritise recoverable information from damaged, deleted or partially corrupted storage. Determine relationships between fragments, assess integrity, and show what can realistically be restored.

Official objectives:
1. **Intelligent Fragment Reconstruction** — fragmented chunks, binary headers, dangling clusters
2. **Data Integrity & Corruption Assessment** — intact / damaged / corrupted portions
3. **Classification & Prioritization** — documents, database logs, photos, system traces
4. **Investigative Decision Support** — what can realistically be restored

Deliverables: working web prototype · demo on synthetic damaged storage · architecture presentation.

---

## 2. Product

**VERDICT** reassembles fragmented files from a damaged storage image and reports, per artifact, exactly what the file format's own integrity data can verify.

Core principle: **ML proposes what to try. The file format and deterministic validation decide what can be trusted.**

- ML only orders candidate next blocks. It never decides correctness.
- Validation runs **during** the search and prunes wrong branches. It is not a post-check.
- Missing data is never generated or filled in. No LLMs anywhere.

---

## 3. Pipeline

```
data/cases/<case>.img
 → ingest    4096B blocks; per block: SHA-256, entropy, printable ratio
 → carve     PNG signature at offset 0 of a block → anchor
 → search    per anchor: parse → candidates → rank → place → validate → accept / backtrack
 → state     PROVEN / PLAUSIBLE / PARTIAL / REJECTED  (rules in §7)
 → classify  signature → type → category   (deterministic, no ML)
 → triage    transparent weighted heuristic (§9)
 → write     data/output/<case>.json
 → FastAPI → Next.js dashboard + Proof Panel
```

Analysis is synchronous. One FastAPI process. JSON files on disk. No database.

Assumption: files start on block boundaries (the generator's allocator is block-based). Carving checks offset 0 of each block only.

---

## 4. Constants — `core/config.py`

```python
BLOCK_SIZE      = 4096
IDAT_CHUNK_SIZE = 2048   # generator writes PNG image data in chunks of this size
TOP_K           = 8      # ranked candidates kept per position
WINDOW          = 256    # locality window in blocks around the last placed block
BUDGET          = 60     # max candidate placements per artifact
```

---

## 5. Reconstruction algorithm (corrected — implement exactly this)

A PNG chunk is `[4B length][4B type][data][4B CRC32 over type+data]`. The length tells the parser how many bytes the current chunk still needs. The CRC lets us check the assembled chunk the moment it is complete.

A chunk may span more than one block, so a CRC failure means the error is **somewhere in the unverified window**, not necessarily in the last block. Backtrack across that window. Everything before the last verified chunk boundary is frozen and never undone.

```
path          = [anchor]
verified_upto = 0          # path[:verified_upto] is CRC-verified and frozen
stack         = []         # (position, remaining ranked candidates)
attempts      = 0

while attempts < BUDGET:
    s = parse_png(bytes_of(path))           # incremental, format-aware

    if s.crc_failed or s.malformed:
        backtrack to the deepest position >= verified_upto
          that still has untried candidates in stack
        if none: return finish(path[:verified_upto], PARTIAL or REJECTED)
        place that position's next candidate; attempts += 1
        continue

    if s.chunk_just_verified:
        verified_upto = len(path)            # freeze prefix
        drop stack entries below verified_upto

    if s.reached_iend:
        return finish(path, final_checks(path))   # checks 5–7 in §6

    cands = candidates(path, used)
    if not cands: return finish(path[:verified_upto], PARTIAL or REJECTED)

    ranked = ranker.order(path, cands)[:TOP_K]    # ML is used here and only here
    stack.push((len(path), ranked[1:]))
    path.append(ranked[0]); attempts += 1

return finish(path[:verified_upto], PARTIAL or REJECTED)
```

**Candidate generation** (`candidates()`):
- exclude blocks already in the current path
- one representative per unique SHA-256 (duplicates are byte-identical; trying both wastes attempts)
- only blocks within ±`WINDOW` of the last placed block; if that is empty, retry once over the whole image
- **no hard entropy filter** — entropy is a ranking feature only (a file's last block contains slack and may have low entropy)

Bytes after IEND in the final block are slack and are ignored.

**Why this is safe:** a verified prefix can never be undone, and nothing unverified is ever reported as evidence. A bad ML ranking can only waste attempts inside the current window.

---

## 6. PNG validation — P0 (`core/validate_png.py`)

**PROVEN requires all seven:**
1. 8-byte signature `89 50 4E 47 0D 0A 1A 0A`
2. IHDR is the first chunk, length 13, CRC valid, legal field values
3. Every chunk: CRC32(type + data) equals the stored CRC
4. IDAT chunks are consecutive in the chunk sequence
5. Concatenated IDAT data inflates with zlib without error (zlib verifies its own Adler-32)
6. Inflated length == `height × (1 + ceil(width × bits_per_pixel / 8))` (non-interlaced)
7. IEND reached, length 0, CRC valid

Malformed chunk = length > 2³¹−1, type not four ASCII letters, or CRC mismatch.

**What PROVEN means:** every byte is consistent with every checksum and length field the format stores. CRC32 and Adler-32 are **not cryptographic** — they detect accidental corruption and misassembly, not deliberate forgery. Never describe them as cryptographic. Chain of custody is separate: SHA-256 of the input image on load and of every output.

**Maximum evidence state per format:**

| Format | Ceiling | Basis |
|---|---|---|
| PNG (P0) | PROVEN | CRC32 per chunk + Adler-32 + declared lengths |
| ZIP / DOCX (P1) | PROVEN per entry | CRC-32 of uncompressed data + declared sizes |
| JPEG (P1) | PLAUSIBLE | No checksum in the format. **Never PROVEN.** |
| TXT / CSV / log (P1) | PLAUSIBLE | Structural heuristics only |

---

## 7. Evidence states

- **PROVEN** — every integrity check the format provides passed and the terminator was reached. For PNG: all seven checks in §6.
- **PLAUSIBLE** — the decoder/structure accepts the full artifact and the terminator was reached, but the format has no checksum sufficient for byte-exact verification. Not reachable for PNG.
- **PARTIAL** — a verified prefix exists (PNG: IHDR + at least one verified IDAT chunk) but the artifact could not be completed. Report verified byte count and the first unverifiable offset.
- **REJECTED** — nothing verified beyond the signature, IHDR invalid, final checks failed, or no valid reconstruction within `BUDGET`.

Never use the word "confidence" for any of these. ML output is shown as a **rank**, never as a probability or confidence.

---

## 8. Corruption, missing data, duplicates, dangling clusters

All derived from ingest + search. No separate subsystem.

- **Unverifiable region:** position where every candidate failed validation. Reported as "missing or corrupted" — the system cannot distinguish these and must not claim to.
- **Duplicates:** blocks with equal SHA-256, linked in output.
- **Dangling clusters:** blocks claimed by no artifact, reported as `unattributed` with their profile.

---

## 9. Classification and triage

**Classification (no ML):** PNG → `image`. P1: JPEG → `image`; ZIP containing `[Content_Types].xml` → `document`, other ZIP → `archive`; text with timestamp pattern → `log`, consistent delimiter → `csv`, else `text`.

**Triage** — a transparent heuristic, never a conclusion:
```
score = w_state * state_value + w_complete * completeness + w_cat * category_value
state_value: PROVEN 1.0, PLAUSIBLE 0.6, PARTIAL 0.4, REJECTED 0.0
completeness: verified_bytes / expected_bytes
```
Weights live in `core/config.py`. UI shows every component and the label **"Triage assistance — not a forensic conclusion."** Fixed weights in P0; sliders in P1.

---

## 10. ML ranker — P1 (system must work without it)

**Task:** order candidate next blocks for the current path. Used only for ordering.

**Interface** (`core/rank.py`):
```python
class Ranker:
    def order(self, path: list[int], candidates: list[int]) -> list[int]: ...
```

**BaselineRanker:** physical offset order — candidates after the last block first, ascending distance; then candidates before it, ascending distance.

**MLRanker:** `GradientBoostingClassifier`; sort by `predict_proba[:, 1]` descending; break ties with baseline order.

**Features** (path tail vs candidate):
1. signed offset delta (candidate − last block)
2. absolute offset delta
3. candidate entropy
4. entropy delta (last block vs candidate)
5. byte-histogram L1 distance (last 512B of path vs first 512B of candidate)
6. candidate printable ratio
7. structural flag: at the offset where the current PNG chunk would end inside the candidate, do the next 8 bytes form a plausible chunk header (sane length + four ASCII letters)?

**Training data:** replay the search along the **true** path of each PNG in `train` cases. At each step, generate candidates exactly as `candidates()` does. Positive = true next block (or any block with identical SHA-256). Negatives = the other candidates in that same set. This matches the inference distribution and gives hard negatives by construction.

**Splits:** multiple cases per corpus, different seeds. Train on `train`, select on `val`, report only on `demo`.

**Leakage rule:** `ml/train.py` reads truth for train/val cases only. Model saved to `ml/models/ranker.joblib`. Demo truth is read only by `evaluation/evaluate.py`.

**Fallback:** if the ML is unfinished or does not beat the baseline, ship with `BaselineRanker` and report the comparison honestly.

---

## 11. Generator — `generator/generate_case.py`

Stdlib + NumPy + Pillow only.

1. **Hand-written PNG writer** (not Pillow's save): raw RGB → `zlib.compress` → split into `IDAT_CHUNK_SIZE`-byte IDAT chunks → `[length][type][data][CRC32]`; include IHDR and IEND. Verify each PNG opens in Pillow.
2. **Corpus:** 6 procedural PNGs (150–300px), 2 text logs, 1 CSV. `--corpus train|val|demo` produces entirely different content families and seed spaces, so corpora share nothing.
3. **Block space:** 2000 × 4096B, pre-filled with residue built from leftover text/image bytes. **Not** `os.urandom`.
4. **First-fit allocator:** write ~half the files sequentially → delete ~one third (mark free, leave bytes) → write remaining files first-fit into free blocks (fragmentation emerges) → partially overwrite 1 deleted file → flip random bytes in 2 blocks of live files → copy 3 blocks into free locations (duplicates). Final-block slack filled with residue.
   *Agreed amendment (task 1):* phase-1 files are separated by 0–3 free blocks (free space left by earlier use), and after the first-fit phase 2 more live files are deleted (recent deletions, not yet reused). Without this, first-fit rarely fragments two PNGs and always overwrites every deleted file, so step "partially overwrite 1 deleted file" has no target.
5. **Never randomly shuffle blocks.**
6. **Output:** `data/cases/<case>.img` and `data/truth/<case>.json` — per file: name, type, sha256, ordered block list, status (`live` / `deleted` / `partially_overwritten` / `overwritten` = every block reused, unrecoverable); plus corrupted and duplicated block indices.
7. **CLI:** `python generator/generate_case.py --case demo01 --corpus demo --seed 37` (seed 37 gives demo01 two fragmented PNGs, one baseline-reachable)
8. **Print:** files written, number fragmented (>1 contiguous run), corrupted count, duplicate count.
9. **Self-test:** rebuild every intact file from its truth block list and confirm sha256 matches.

---

## 12. Truth isolation — hard rule

The recovery engine receives **only** the `.img` path.
`data/truth/` is written only by the generator and read only by `ml/train.py` and `evaluation/evaluate.py`.
`grep -rn "truth" core/ api/` must return nothing.

---

## 13. Evaluation — `evaluation/evaluate.py`

Runs the pipeline on demo cases with `--ranker baseline` and `--ranker ml`. Matches each artifact to a truth file by anchor block (= truth file's first block). Writes `data/output/metrics.json`. The API serves this file verbatim.

Metrics (always report numerator and denominator):
- **exact_recovery:** artifacts whose SHA-256 equals the truth sha256 / recoverable truth PNGs
- **false_proven:** PROVEN artifacts whose SHA-256 does not match truth / PROVEN artifacts. Expected 0 — if not, it's a bug; report it, don't hide it
- **attempts_per_artifact:** mean candidate placements, baseline vs ml ← the metric that shows whether ML helps
- **top1 / top5:** rank of the true successor within each step's ranked candidates (read from trail)
- **partial:** count of PARTIAL artifacts, verified bytes / file size
- **corruption_localization:** truth-corrupted blocks inside PNGs that fall within a reported unverifiable region / total such blocks

**Never invent, estimate or hardcode a number.** Every number in the UI, README or slides comes from `metrics.json`.

---

## 14. Output data model — `data/output/<case>.json`

```json
{
  "case_id": "demo01",
  "image_sha256": "…",
  "ranker": "baseline",
  "summary": {"artifacts": 6, "PROVEN": 0, "PLAUSIBLE": 0, "PARTIAL": 0,
              "REJECTED": 0, "unattributed_blocks": 0, "duplicate_blocks": 0},
  "artifacts": [{
    "id": "art_003",
    "type": "png", "category": "image",
    "anchor_block": 412,
    "assembly": [412, 413, 891],
    "state": "PROVEN",
    "verified_bytes": 9812, "expected_bytes": 9812,
    "unverifiable_from": null,
    "checks": [{"check": "IHDR_crc", "ok": true},
               {"check": "IDAT_1_crc", "stored": "0x1D4C77E0",
                "computed": "0x1D4C77E0", "ok": true}],
    "trail": [
      {"step": 3, "event": "PLACE", "block": 655, "rank": 1, "ranked": [655, 891, 12]},
      {"step": 3, "event": "VERIFY_FAIL", "chunk": "IDAT_2", "reason": "crc_mismatch"},
      {"step": 3, "event": "BACKTRACK", "block": 655},
      {"step": 3, "event": "PLACE", "block": 891, "rank": 2},
      {"step": 3, "event": "VERIFY_OK", "chunk": "IDAT_2"}
    ],
    "triage": {"score": 0.71, "components":
               {"state": 1.0, "completeness": 1.0, "category": 0.5}}
  }],
  "unattributed": [{"block": 1777, "entropy": 4.2, "printable": 0.93}]
}
```

`trail` drives the Proof Panel.

---

## 15. API — `api/main.py`

```
POST /case/analyze   {"case_id": "demo01", "ranker": "baseline"}  → runs pipeline, returns summary
GET  /case/{case_id}                              → full case JSON
GET  /artifact/{case_id}/{artifact_id}/preview    → image/png (verified prefix for PARTIAL)
GET  /metrics                                     → data/output/metrics.json
```

CORS: allow `http://localhost:3000`. No other routes.

---

## 16. Frontend — `frontend/`

Next.js (App Router) + TypeScript + Tailwind. API base URL from `NEXT_PUBLIC_API_URL`.

P0 screens: case summary (state counts, unattributed/duplicate counts) · artifact list with state badges and triage score · artifact detail with preview · **Proof Panel** rendering `trail` (placed candidate, rank, verify result, backtrack) and `checks` · metrics view.
Every view has loading, empty and error states.

**Offline venue:** no `next/font/google`, no CDNs, no external requests. System font stack only.

---

## 17. Stack

Python 3.11 · FastAPI · uvicorn · NumPy · scikit-learn · joblib · Pillow · zlib · zipfile · Next.js · React · TypeScript · Tailwind · JSON files.
**Ask before adding any other dependency.**

Run:
```
uvicorn api.main:app --reload --port 8000
cd frontend && npm run dev          # port 3000
```

---

## 18. Repository

```
verdict/
├── CLAUDE.md
├── README.md
├── generator/generate_case.py     # allocator simulation → .img + truth
├── core/
│   ├── config.py                  # constants, triage weights
│   ├── ingest.py                  # blocks, SHA-256, entropy, printable ratio, dedup map
│   ├── carve.py                   # PNG anchors; classification
│   ├── validate_png.py            # incremental parser + 7 checks
│   ├── search.py                  # §5 algorithm, trail events
│   ├── rank.py                    # BaselineRanker, MLRanker
│   ├── triage.py                  # §9
│   └── pipeline.py                # ingest→carve→search→state→classify→triage→write JSON
├── ml/
│   ├── features.py                # §10 features
│   ├── train.py                   # reads train/val truth only
│   └── models/
├── evaluation/evaluate.py         # §13
├── api/main.py                    # §15
├── frontend/
└── data/
    ├── cases/                     # .img files
    ├── truth/                     # generator + train.py + evaluate.py only
    └── output/                    # case JSON, metrics.json
```

`.gitignore`: `node_modules/`, `.next/`, `venv/`, `__pycache__/`, `.env*`, `data/cases/*.img`.

---

## 19. Scope

**P0:** generator · ingest · PNG carve · PNG validation · constrained search with backtracking · BaselineRanker · evaluate.py · API · minimal dashboard · Proof Panel
**P1:** MLRanker + baseline comparison · PARTIAL preview · block map grid · triage sliders · ZIP/DOCX · JPEG
**P2:** hex viewer · report export · more formats
**Never:** database · auth · Docker · cloud · websockets · job queues · deep learning · LLMs · real filesystem (NTFS/FAT) parsing · arbitrary user upload

---

## 20. Implementation sequence

Do **one task at a time**. Do not start the next until the current one meets its definition of done.

1. **Generator** — summary printed, self-test passes, ≥2 PNGs fragmented.
2. **Ingest + carve** — prints PNG anchor block indices for `demo01`.
3. **validate_png** — accepts an intact PNG from disk; rejects one with a flipped byte and names the failed check.
4. **Search + BaselineRanker + pipeline** — **MILESTONE 1:** at least one fragmented PNG in `demo01` ends PROVEN, and `evaluate.py` confirms its SHA-256 matches truth. No UI, no ML.
5. **evaluate.py** — writes `metrics.json` with baseline numbers.
6. **API** — all four routes return real data.
7. **Frontend + Proof Panel** — list, preview, trail visible.
8. **MLRanker (P1)** — features, train, baseline vs ml in `metrics.json`.
9. **P1 extras** in order: PARTIAL preview → block map → triage sliders → ZIP/DOCX → JPEG.

If behind: cut from the bottom of the list. Never cut 1–7.

---

## 21. Working agreement for Claude Code

The developer is a beginner who must explain this code to judges.

- After each task, report: files changed, how to run, and the **actual** output you observed.
- After writing each file, explain it in ≤5 plain-English lines.
- Run the code before claiming it works.
- Suggest a git commit message after each working step.
- Keep functions small and readable; no clever abstractions.
- Do not refactor working code unless asked.
- If a task is dragging, propose a cut from §19 instead of expanding scope.

---

## 22. Never do these

- Redesign the architecture, rename evidence states, or add formats before Milestone 1
- Read `data/truth/` from `core/` or `api/`
- Let ML decide correctness, or show ML output as a probability or "confidence"
- Call CRC32/Adler-32 cryptographic, or label JPEG PROVEN
- Generate, guess or fill missing bytes
- Randomly shuffle blocks in the generator
- Invent, estimate or hardcode any metric
- Build frontend before Milestone 1
- Make network requests at runtime
- Add dependencies without asking
