"use client";
// VERDICT dashboard: one page. Loads both rankers' results for the chosen case
// so the table can compare them; the toggle picks which one the cards and
// detail panel show.
import { useEffect, useState } from "react";
import { ArtifactTable, byAnchor, type RowOrder } from "@/components/ArtifactTable";
import { DetailPanel } from "@/components/DetailPanel";
import { MetricsPanel } from "@/components/MetricsPanel";
import { Card, ErrorBox, Segmented, Skeleton, STATE_TONE, type Remote } from "@/components/ui";
import {
  CASES,
  fetchCase,
  fetchMetrics,
  type Artifact,
  type CaseReport,
  type EvidenceState,
  type Metrics,
  type RankerName,
} from "@/lib/api";

function useRemote<T>(load: () => Promise<T>, deps: unknown[]): Remote<T> {
  const [state, setState] = useState<Remote<T>>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    load()
      .then((data) => !cancelled && setState({ status: "ok", data }))
      .catch((err: Error) => !cancelled && setState({ status: "error", message: err.message }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

function anchorList(anchors: number[]): string {
  return `${anchors.length === 1 ? "anchor" : "anchors"} ${anchors.join(", ")}`;
}

// One sentence built only from the case data: states, anchors and triage scores.
function caseSentence(artifacts: Artifact[]): string {
  if (artifacts.length === 0) return "No PNG anchors were carved from this image.";
  const anchorsIn = (state: EvidenceState) => artifacts.filter((a) => a.state === state).map((a) => a.anchor_block);
  const proven = anchorsIn("PROVEN");
  const partial = anchorsIn("PARTIAL");
  const rejected = anchorsIn("REJECTED");
  const parts: string[] = [];
  if (proven.length) {
    parts.push(`${proven.length} PROVEN: every CRC and length check passed (${anchorList(proven)}).`);
  }
  if (partial.length) {
    parts.push(
      `${partial.length} PARTIAL (${anchorList(partial)}): a verified prefix was recovered; ` +
        "the rest could not be verified (missing, corrupted, or not found by the search).",
    );
  }
  if (rejected.length) {
    parts.push(`${rejected.length} REJECTED (${anchorList(rejected)}): no image data could be verified.`);
  }
  const best = Math.max(...artifacts.map((a) => a.triage.score));
  const top = artifacts.filter((a) => a.triage.score === best).map((a) => a.anchor_block);
  const ties = top.length > 1 ? `, tied with ${anchorList(top.slice(1))}` : "";
  parts.push(`Start with anchor ${top[0]} (highest triage score, ${best.toFixed(2)}${ties}).`);
  return parts.join(" ");
}

function SummaryCards({ report, ranker }: { report: Remote<CaseReport>; ranker: RankerName }) {
  const title = `Case summary · ${ranker === "ml" ? "ML" : "baseline"} ranker`;
  if (report.status === "loading") {
    return (
      <Card title={title}>
        <Skeleton className="mb-4 h-4 w-full" />
        <Skeleton className="mb-5 h-4 w-3/4" />
        <div className="grid grid-cols-3 gap-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
        </div>
      </Card>
    );
  }
  if (report.status === "error") return <Card title={title}><ErrorBox message={report.message} /></Card>;
  const s = report.data.summary;
  const tiles: [EvidenceState, number][] = [["PROVEN", s.PROVEN], ["PARTIAL", s.PARTIAL], ["REJECTED", s.REJECTED]];
  return (
    <Card title={title}>
      <p className="mb-5 text-[15px] leading-relaxed text-ink">{caseSentence(report.data.artifacts)}</p>
      <div className="grid grid-cols-3 gap-3">
        {tiles.map(([label, count]) => (
          <div key={label} className="rounded-xl border border-line bg-panel-2 p-4">
            <div className={`font-mono text-4xl font-medium leading-none ${STATE_TONE[label].text}`}>{count}</div>
            <div className="mt-3 flex items-center gap-2 text-xs">
              <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${STATE_TONE[label].dot}`} />
              <span className="font-semibold tracking-wider text-ink">{label}</span>
              <span className="text-muted">of <span className="font-mono">{s.artifacts}</span></span>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-ink-2">
        <span className="font-mono text-ink">{s.unattributed_blocks}</span> unattributed blocks ·{" "}
        <span className="font-mono text-ink">{s.duplicate_blocks}</span> blocks share their SHA-256 with another block ·
        image SHA-256 <span className="font-mono text-ink">{report.data.image_sha256.slice(0, 16)}…</span>
      </p>
    </Card>
  );
}

// First row (disk order) whose evidence state differs between the rankers;
// otherwise the first row. Null until both reports have loaded.
function initialAnchor(baseline: Remote<CaseReport>, ml: Remote<CaseReport>): number | null {
  if (baseline.status === "loading" || ml.status === "loading") return null;
  const base = byAnchor(baseline);
  const learned = byAnchor(ml);
  const anchors = [...new Set([...base.keys(), ...learned.keys()])].sort((a, b) => a - b);
  const differing = anchors.find((a) => base.get(a) && learned.get(a) && base.get(a)!.state !== learned.get(a)!.state);
  return differing ?? anchors[0] ?? null;
}

export default function Home() {
  const [caseId, setCaseId] = useState(CASES[0]);
  const [ranker, setRanker] = useState<RankerName>("ml");
  const [picked, setPicked] = useState<{ caseId: string; anchor: number } | null>(null);
  const [order, setOrder] = useState<RowOrder>("disk");

  const baseline = useRemote(() => fetchCase(caseId, "baseline"), [caseId]);
  const ml = useRemote(() => fetchCase(caseId, "ml"), [caseId]);
  const metrics = useRemote<Metrics>(fetchMetrics, []);
  const active = ranker === "ml" ? ml : baseline;
  // The user's choice wins; until they pick a row in this case, auto-select one.
  const anchor = picked?.caseId === caseId ? picked.anchor : initialAnchor(baseline, ml);

  return (
    <main className="mx-auto w-full max-w-[1760px] px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-line pb-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-[0.32em] text-ink">VERDICT</h1>
          <p className="mt-1 text-sm text-ink-2">
            ML proposes what to try. The file format&apos;s own checksums decide what can be trusted.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs uppercase tracking-[0.12em] text-muted">
            Case
            <select
              value={caseId}
              onChange={(e) => setCaseId(e.target.value)}
              className="rounded-full border border-line bg-panel-2 px-3 py-1.5 font-mono text-sm normal-case tracking-normal text-ink transition-colors duration-150 hover:border-ink-2/50"
            >
              {CASES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <Segmented
            label="Ranker"
            options={[["baseline", "Baseline"], ["ml", "ML"]]}
            value={ranker}
            onChange={setRanker}
          />
        </div>
      </header>

      <div className="grid gap-5 min-[1400px]:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] min-[1400px]:items-start">
        <div className="min-w-0 space-y-5">
          <SummaryCards report={active} ranker={ranker} />
          <ArtifactTable
            caseId={caseId}
            baseline={baseline}
            ml={ml}
            active={ranker}
            order={order}
            onOrderChange={setOrder}
            selectedAnchor={anchor}
            onSelect={(a) => setPicked({ caseId, anchor: a })}
          />
        </div>
        <div className="min-w-0 min-[1400px]:sticky min-[1400px]:top-6 min-[1400px]:max-h-[calc(100vh-3rem)] min-[1400px]:overflow-y-auto min-[1400px]:pr-1">
          <DetailPanel caseId={caseId} ranker={ranker} report={active} anchor={anchor} />
        </div>
      </div>

      <div className="mt-5">
        <MetricsPanel metrics={metrics} />
      </div>
    </main>
  );
}
