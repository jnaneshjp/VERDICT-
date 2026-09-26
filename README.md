# VERDICT

VERDICT rebuilds deleted and fragmented PNG pictures from a damaged disk image and, for each one,
says exactly how much of it the file format's own integrity data can vouch for. It scans every
4 KB block for the PNG signature, then grows each file one block at a time: after every block it
re-checks the PNG's built-in checksums, keeps what verifies, and undoes what doesn't. The result
for each file is one of four evidence states, a byte count of what was verified, and a step-by-step
trail (the "Proof Panel") showing every block tried, its rank, and whether the checksum accepted it.
Nothing is ever guessed or filled in, and no LLM is involved anywhere.

**Explainer:** open [`docs/verdict-explainer.html`](docs/verdict-explainer.html) in a browser for a
six-step animated walkthrough (arrow keys to step, `A` for autoplay). It works offline.

## Core idea

> **ML proposes what to try; the file format's checksums decide what can be trusted.**

- A PNG is a series of chunks, and every chunk carries a CRC32 of its contents. The compressed
  picture data also carries an Adler-32, and the header declares the exact image size.
- When a chunk is complete, VERDICT checks its CRC immediately. A match freezes everything up to
  that point; it is never undone. A mismatch means the last guess was wrong: back up and try the
  next candidate block.
- The ML ranker (a scikit-learn gradient-boosting model over 7 simple block features) only decides
  the **order** in which nearby blocks are tried. A bad ranking can only waste attempts; it can never
  make a wrong block pass a checksum. ML output is shown as a rank, never as a probability.
- CRC32 and Adler-32 are **not cryptographic**. They catch accidental corruption and wrong
  assembly, not deliberate forgery. Chain of custody is separate: the SHA-256 of the input image
  and of every output is recorded.

## The four evidence states

| State | Meaning |
|---|---|
| **PROVEN** | Every check the format provides passed and the end marker was reached. For PNG, all seven: signature, header chunk, every chunk CRC, picture chunks consecutive, picture data decompresses (Adler-32), decompressed size matches the header, end chunk valid. |
| **PLAUSIBLE** | The whole file parses and ends properly, but the format has no checksum strong enough for byte-exact verification (e.g. JPEG, text). **Never reachable for PNG.** |
| **PARTIAL** | A verified prefix exists (header + at least one verified picture chunk) but the rest could not be completed. We report the verified byte count and the first unverifiable offset, which is described as "missing, corrupted, or not found by the search"; the system cannot tell those apart and doesn't claim to. |
| **REJECTED** | Nothing verified beyond the signature, or the final whole-file checks failed. |

## Results on 50 held-out test disks

Copied from [`data/output/metrics.json`](data/output/metrics.json) (`rankers.<name>.totals`).
50 test disks (`demo01`–`demo50`), 238 PNG start points found, all of which the evaluator matched to a real file.

| | Baseline (nearest block first) | ML ranker |
|---|---|---|
| **Intact files recovered exactly** (PROVEN and SHA-256 equals the original) | **75 / 92** | **92 / 92** |
| **False PROVEN** (PROVEN but SHA-256 differs from the original) | **0** | **0** |
| PROVEN artifacts whose SHA-256 matches *some* original file on that disk | 75 / 75 | 92 / 92 |
| **PARTIAL prefixes** whose verified bytes equal the original's first bytes | **163 / 163** | **146 / 146** |
| State counts (PROVEN / PLAUSIBLE / PARTIAL / REJECTED) | 75 / 0 / 163 / 0 | 92 / 0 / 146 / 0 |
| Exact recovery over all 238 PNG start points | 75 / 238 | 92 / 238 |
| Candidate placements (attempts) per artifact | 13.8866 (3305 total) | 12.521 (2980 total) |
| True next block ranked 1st | 469 / 686 | 710 / 732 |
| True next block in top 5 | 645 / 686 | 731 / 732 |

How to read this:

- **"Intact"** means every byte of the original file is still on the disk (68 live files plus 24
  deleted ones whose blocks weren't reused). These are the files it's possible to prove. The other
  146 start points belong to pictures with deliberately flipped bytes (100) or partly overwritten by
  newer files (46). For those, PARTIAL with a correct prefix is the honest answer, and that's why
  exact recovery over all 238 can't reach 238. (The 68 / 24 / 100 / 46 split is counted from the
  `damage_class` of each artifact in `metrics.json`.)
- The ML ranker's gain comes from fragmented files whose next piece sits beyond the 8 nearest
  blocks. Nearest-first never reaches it and stops at PARTIAL; the ML ranker tries it early.
- Top-1 / top-5 denominators differ between rankers because each ranker's search takes different
  paths, so it passes through a different number of steps.
- Every number in this README, the dashboard and the explainer comes from `metrics.json`. None is
  typed in by hand.

## How we validated

- **Separate train / val / test disks.** Each disk is generated from a corpus name and a seed.
  The three corpora use different picture families and seed spaces, so they share no content.
  - train: `train01`–`train08` (corpus `train`, seeds 1–8). Used to fit the model.
  - val: `val101`–`val103` (corpus `val`, seeds 101–103). Used to pick model settings.
  - test: `demo01`–`demo50` (corpus `demo`, seeds 37–86). **Never used in training or model
    selection**; all results above are on these.
- **Truth isolation.** The generator writes an answer key to `data/truth/`. Only `ml/train.py`
  (train/val only) and `evaluation/evaluate.py` may read it. The recovery engine (`core/`) and API
  (`api/`) get only the `.img` file. This is checked two ways: `grep -rn "truth" core/ api/`
  returns nothing, and `verify_task5.py` includes an automated "truth isolation" check that fails if
  any `core/` file references `data/truth` or a truth JSON.
- **The evaluator bug we found, and the cross-check.** An earlier evaluator reported 2 false PROVEN
  and 3 prefix mismatches per ranker. We traced it: when a newer picture had overwritten an older one
  starting at the same block, the evaluator compared the recovered file against the *older,
  overwritten* file. The recovery was right; the answer-key lookup was wrong. We fixed the matching
  (use the file whose first block was not overwritten, commit `cfcc893`) and, so the fix wasn't just
  trusted, added an independent check that doesn't depend on matching at all: every PROVEN artifact's
  SHA-256 is compared against the SHA-256 of **every** original file on that disk
  (commit `ecdf477`). Result: 75/75 baseline and 92/92 ML PROVEN artifacts are byte-identical to a
  real original.
- **PARTIAL prefixes are checked too.** The evaluator rebuilds each original file from the same
  corpus and seed (confirmed against the stored SHA-256) and compares every PARTIAL artifact's
  verified bytes with the same-length start of the original.

## Honest limits

- **Synthetic disks only.** All disks come from our own generator, which simulates a block
  allocator (write, delete, reuse, partial overwrite, byte flips, duplicate blocks). It has not been
  run on a real drive or a real filesystem image.
- **PNG only.** Carving, validation and reconstruction are implemented for PNG. ZIP/DOCX, JPEG and
  text formats are designed for (JPEG and text could at most reach PLAUSIBLE) but not built.
- Files are assumed to start at the beginning of a 4 KB block, matching the generator.
- The search tries at most 8 candidates per position, within ±256 blocks, and at most 60 placements
  per file. A piece further away than that is reported as unverifiable, not as missing.
- The triage score in the dashboard is a transparent weighted formula, labelled
  *"Triage assistance — not a forensic conclusion."*

## Run it

Requirements: Python 3.11 and Node.js. Everything runs offline.

### 1. Install

```
python -m venv venv
venv\Scripts\activate                 # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 2. Regenerate the disk images

`.img` files are not in git (2000 × 4096 bytes each). Their answer keys (`data/truth/`), the trained
model (`ml/models/ranker.joblib`) and all analysis outputs (`data/output/`) *are* in git, so the
dashboard works without this step. You need the images to re-run the analysis or preview pictures.
The generator is deterministic: the same corpus and seed give the same image, and its self-test
confirms every intact file's SHA-256.

One disk:

```
python generator/generate_case.py --case demo01 --corpus demo --seed 37
```

All 61 disks, bash (macOS/Linux/Git Bash):

```
for i in $(seq 1 8);   do python generator/generate_case.py --case train0$i --corpus train --seed $i; done
for s in 101 102 103;  do python generator/generate_case.py --case val$s    --corpus val   --seed $s; done
for n in $(seq 1 50);  do python generator/generate_case.py --case demo$(printf %02d $n) --corpus demo --seed $((n+36)); done
```

All 61 disks, PowerShell:

```
1..8     | % { python generator/generate_case.py --case ("train{0:D2}" -f $_) --corpus train --seed $_ }
101..103 | % { python generator/generate_case.py --case "val$_" --corpus val --seed $_ }
1..50    | % { python generator/generate_case.py --case ("demo{0:D2}" -f $_) --corpus demo --seed ($_ + 36) }
```

Test disk `demoNN` always uses seed `NN + 36`; each disk's seed is also stored in `data/truth/<case>.json`.

### 3. (Optional) Re-run analysis, training and evaluation

```
python -m core.pipeline --case demo01 --ranker ml    # one disk → data/output/demo01_ml.json
python ml/train.py                                    # retrain on train01-08, select on val101-103
python evaluation/evaluate.py --ranker baseline ml --case demo01 demo02 ... demo50
python verify_task5.py                                # evaluator checks incl. truth isolation
```

`evaluate.py` takes the case list explicitly. In bash you can generate it with
`--case $(for n in $(seq 1 50); do printf "demo%02d " $n; done)`. It rewrites
`data/output/metrics.json`.

### 4. Start the dashboard

Two terminals, both from the repository root:

```
python -m uvicorn api.main:app --reload --port 8000
cd frontend && npm run dev
```

Open http://localhost:3000. The API address defaults to `http://localhost:8000`; override it with
`NEXT_PUBLIC_API_URL`. The dashboard uses system fonts only and makes no external requests
(to switch off Next.js telemetry as well: `npx next telemetry disable`).

## Repository map

```
generator/generate_case.py   synthetic damaged disk + answer key
core/                        ingest, carve, PNG validation, search, rankers, triage, pipeline
ml/                          features, training (train/val only), saved model
evaluation/evaluate.py       scores results against the answer key → metrics.json
api/main.py                  FastAPI: analyze, case, preview, metrics
frontend/                    Next.js dashboard + Proof Panel
docs/verdict-explainer.html  animated walkthrough
CLAUDE.md                    full design spec
```
