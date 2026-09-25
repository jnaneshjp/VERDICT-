# VERDICT

Reassembles fragmented files from a damaged storage image and reports, per artifact, exactly what the
file format's own integrity data can verify. ML only proposes the order to try candidate blocks;
the file format's checksums and lengths decide what can be trusted. See `CLAUDE.md` for the full spec.

## Setup

```
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Step 1 — generate a synthetic damaged disk

```
python generator/generate_case.py --case demo01 --corpus demo --seed 37
```

Writes `data/cases/demo01.img` (2000 × 4096-byte blocks) and `data/truth/demo01.json`
(where every file really lives). Truth is read only by `ml/train.py` and `evaluation/evaluate.py`,
never by `core/` or `api/`.

## Run the demo

Results for `demo01`–`demo05` (both rankers) are already in `data/output/`, so nothing waits.
Open two terminals in the repository root:

```
python -m uvicorn api.main:app --reload --port 8000
cd frontend && npm run dev
```

Then open http://localhost:3000. (First time only: `cd frontend && npm install`.)
The API address defaults to `http://localhost:8000`; override it with `NEXT_PUBLIC_API_URL`.
Everything runs offline: system fonts only, and Next.js telemetry is disabled (`npx next telemetry disable`).
