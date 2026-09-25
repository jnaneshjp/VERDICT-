// Baseline vs ML totals from /metrics. Every number is shown as numerator / denominator.
import type { ReactNode } from "react";
import type { Metrics, Ratio, RankerTotals } from "@/lib/api";
import { Card, Empty, ErrorBox, Loading, type Remote } from "./ui";

function Fraction({ n, d }: { n: number; d: number }) {
  return (
    <span className="font-mono text-ink">
      {n} <span className="text-muted">/</span> {d}
    </span>
  );
}

const ROWS: { label: string; cell: (t: RankerTotals) => ReactNode }[] = [
  { label: "Intact PNGs recovered exactly", cell: (t) => <R r={t.known_intact_recovered} /> },
  { label: "Exact recovery (all carved anchors)", cell: (t) => <R r={t.exact_recovery} /> },
  { label: "PROVEN artifacts", cell: (t) => <Fraction n={t.state_counts.PROVEN} d={t.artifacts} /> },
  { label: "PARTIAL artifacts", cell: (t) => <Fraction n={t.state_counts.PARTIAL} d={t.artifacts} /> },
  { label: "PARTIAL prefixes matching the original", cell: (t) => <R r={t.partial_prefix_match} /> },
  { label: "False PROVEN (SHA-256 ≠ original)", cell: (t) => <Fraction n={t.false_verified_count} d={t.state_counts.PROVEN} /> },
  { label: "Candidate placements (total / artifacts)", cell: (t) => <Fraction n={t.total_attempts} d={t.artifacts} /> },
  { label: "True next block ranked #1", cell: (t) => <R r={t.top1} /> },
  { label: "True next block in top 5", cell: (t) => <R r={t.top5} /> },
];

function R({ r }: { r: Ratio }) {
  return <Fraction n={r.numerator} d={r.denominator} />;
}

export function MetricsPanel({ metrics }: { metrics: Remote<Metrics> }) {
  const title = "Evaluation — baseline vs ML";
  if (metrics.status === "loading") return <Card title={title}><Loading what="metrics" /></Card>;
  if (metrics.status === "error") return <Card title={title}><ErrorBox message={metrics.message} /></Card>;
  const { baseline, ml } = metrics.data.rankers;
  if (!baseline && !ml) return <Card title={title}><Empty message="metrics.json has no ranker results yet." /></Card>;

  return (
    <Card title={title} right={
        <span className="rounded-full border border-line bg-panel-2 px-3 py-1 text-xs text-ink-2">
          <span className="font-mono text-ink">{metrics.data.cases.length}</span> test disks, never used in training
        </span>
      }>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[480px] text-left text-sm">
          <thead className="text-[11px] uppercase tracking-[0.12em] text-muted">
            <tr>
              <th className="pb-2 pr-3 font-medium">Metric</th>
              <th className="pb-2 pr-3 font-medium">Baseline</th>
              <th className="pb-2 font-medium">ML</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map(({ label, cell }) => (
              <tr key={label} className="border-t border-line transition-colors duration-150 hover:bg-panel-2/60">
                <td className="py-2 pr-3 text-ink-2">{label}</td>
                <td className="py-2 pr-3">{baseline ? cell(baseline.totals) : "—"}</td>
                <td className="py-2">{ml ? cell(ml.totals) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted">
        Top-1/top-5 count only steps where the path so far was correct, so their denominators differ between rankers.
      </p>
    </Card>
  );
}
