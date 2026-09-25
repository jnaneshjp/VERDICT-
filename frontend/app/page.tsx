"use client";
// VERDICT dashboard: one page. Loads both rankers' results for the chosen case
// so the table can compare them; the toggle picks which one the cards and
// detail panel show.
import { useEffect, useState } from "react";
import { ArtifactTable, type RowOrder } from "@/components/ArtifactTable";
import { DetailPanel } from "@/components/DetailPanel";
import { MetricsPanel } from "@/components/MetricsPanel";
import { Card, ErrorBox, Loading, type Remote } from "@/components/ui";
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
  const title = `Case summary (${ranker === "ml" ? "ML" : "baseline"} ranker)`;
  if (report.status === "loading") return <Card title={title}><Loading what="summary" /></Card>;
  if (report.status === "error") return <Card title={title}><ErrorBox message={report.message} /></Card>;
  const s = report.data.summary;
  const tiles: [string, number, string][] = [
    ["PROVEN", s.PROVEN, "border-emerald-500/50 text-emerald-300"],
    ["PARTIAL", s.PARTIAL, "border-amber-500/50 text-amber-300"],
    ["REJECTED", s.REJECTED, "border-red-500/50 text-red-300"],
  ];
  return (
    <Card title={title}>
      <p className="mb-3 text-sm leading-relaxed text-zinc-200">{caseSentence(report.data.artifacts)}</p>
      <div className="grid grid-cols-3 gap-3">
        {tiles.map(([label, count, tone]) => (
          <div key={label} className={`rounded-lg border bg-zinc-950 p-3 ${tone}`}>
            <div className="text-3xl font-bold">{count}</div>
            <div className="text-xs font-semibold tracking-wide">{label} <span className="text-zinc-500">of {s.artifacts}</span></div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-zinc-500">
        {s.unattributed_blocks} unattributed blocks · {s.duplicate_blocks} blocks share their SHA-256 with another block ·
        image SHA-256 <span className="font-mono">{report.data.image_sha256.slice(0, 16)}…</span>
      </p>
    </Card>
  );
}

export default function Home() {
  const [caseId, setCaseId] = useState(CASES[0]);
  const [ranker, setRanker] = useState<RankerName>("ml");
  const [anchor, setAnchor] = useState<number | null>(null);
  const [order, setOrder] = useState<RowOrder>("disk");

  const baseline = useRemote(() => fetchCase(caseId, "baseline"), [caseId]);
  const ml = useRemote(() => fetchCase(caseId, "ml"), [caseId]);
  const metrics = useRemote<Metrics>(fetchMetrics, []);
  const active = ranker === "ml" ? ml : baseline;

  return (
    <main className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-widest">VERDICT</h1>
          <p className="text-xs text-zinc-400">
            ML proposes what to try. The file format&apos;s own checksums decide what can be trusted.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-sm text-zinc-400">
            Case{" "}
            <select
              value={caseId}
              onChange={(e) => {
                setCaseId(e.target.value);
                setAnchor(null);
              }}
              className="ml-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-100"
            >
              {CASES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <div className="flex overflow-hidden rounded border border-zinc-700 text-sm">
            {(["baseline", "ml"] as RankerName[]).map((name) => (
              <button
                key={name}
                onClick={() => setRanker(name)}
                className={`px-3 py-1 ${ranker === name ? "bg-zinc-100 text-zinc-900" : "bg-zinc-900 text-zinc-300 hover:bg-zinc-800"}`}
              >
                {name === "ml" ? "ML" : "Baseline"}
              </button>
            ))}
          </div>
        </div>
      </header>

      <SummaryCards report={active} ranker={ranker} />
      <ArtifactTable
        caseId={caseId}
        baseline={baseline}
        ml={ml}
        active={ranker}
        order={order}
        onOrderChange={setOrder}
        selectedAnchor={anchor}
        onSelect={setAnchor}
      />
      <DetailPanel caseId={caseId} ranker={ranker} report={active} anchor={anchor} />
      <MetricsPanel metrics={metrics} />
    </main>
  );
}
