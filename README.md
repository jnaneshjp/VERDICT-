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
- The dashboard reads its numbers from `metrics.json`, and the numbers in this README were copied
  from it. The explainer's figures (`docs/verdict-explainer.html`) were copied from `metrics.json`
  by hand and checked against it.

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

## DOCX prototype

An early extension using the same search and backtracking, with a ZIP validator plugged in. Each ZIP
entry is its own verified unit: the CRC-32 and size of its uncompressed data are checked as soon as the
entry is complete. PROVEN also needs a central directory that matches every entry, and the End of
Central Directory record. It was tested on 10 separate DOCX disks, `docx01`–`docx10` (corpus `docx`,
seeds 201–210). The numbers below are copied from
[`data/output/metrics_docx.json`](data/output/metrics_docx.json).

| | Baseline | DOCX ML ranker |
|---|---|---|
| Intact DOCX files recovered exactly | 9 / 11 | 10 / 11 |
| False PROVEN | 0 / 9 | 0 / 10 |
| PARTIAL prefixes matching the original | 18 / 18 | 17 / 17 |

- Only 1 of the baseline's 9 PROVEN DOCX files was fragmented (`docx09`, blocks 0, 1, 2, 10). The
  other 8 sit in one contiguous run, so these disks test fragmentation much less than the PNG disks.
- The DOCX ML ranker is a separate model (`ml/models/ranker_docx.joblib`) trained on its own disks
  (`docx_train` seeds 301–308, chosen on `docx_val` seeds 401–403). The PNG model is never used for
  DOCX. Its result is **mixed on a small sample**: it recovers the 2 intact files the baseline missed
  (`docx05` and `docx06`) but loses the one fragmented file the baseline got (`docx09`), whose true
  next block fell outside its top 8.
- Why: a ZIP gives the ranker few checkpoints. A PNG has a CRC every 2 KB, but a DOCX has only a
  handful of entries, and most of the bytes sit in one large compressed entry. The ZIP clue "does the
  next entry header appear where this entry should end?" only applies at those few boundaries. The
  other clue, "does inflation continue without error?", also passes for most wrong blocks. So the model
  mostly relies on distance, and with 49 training steps it has little else to learn from.
- In the dashboard's ML column, every DOCX row is labelled "DOCX model", or "baseline fallback" if the
  DOCX model was not available.

Reproduce: generate the disks (`--corpus docx` with seeds 201–210 as `docx01`–`docx10`;
`--corpus docx_train` seeds 301–308 as `docxtrain301`–`docxtrain308`; `--corpus docx_val` seeds
401–403 as `docxval401`–`docxval403`), then run `python ml/train_docx.py` and
`python evaluation/evaluate_docx.py`.

## Honest limits

- **Synthetic disks only.** All disks come from our own generator, which simulates a block
  allocator (write, delete, reuse, partial overwrite, byte flips, duplicate blocks). It has not been
  run on a real drive or a real filesystem image.
- **PNG, plus an early DOCX prototype.** PNG is the evaluated format (50 test disks). ZIP/DOCX works
  end to end but has only been tested on 10 small synthetic disks with little fragmentation, and it
  rejects ZIP data descriptors, ZIP64 and encryption. JPEG and text formats are designed for (they
  could at most reach PLAUSIBLE) but not built.
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
ml/                          features, training (train/val only), saved models (PNG, DOCX)
evaluation/evaluate.py       scores results against the answer key → metrics.json
evaluation/evaluate_docx.py  DOCX prototype, baseline vs ML → metrics_docx.json
api/main.py                  FastAPI: analyze, case, preview, metrics
frontend/                    Next.js dashboard + Proof Panel
docs/verdict-explainer.html  animated walkthrough
CLAUDE.md                    full design spec
```
