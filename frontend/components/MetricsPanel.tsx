// Baseline vs ML totals from /metrics. Every number is shown as numerator / denominator.
import type { ReactNode } from "react";
import type { Metrics, Ratio, RankerTotals } from "@/lib/api";
import { Card, Empty, ErrorBox, Loading, type Remote } from "./ui";

function Fraction({ n, d }: { n: number; d: number }) {
  return <span className="font-mono">{n} / {d}</span>;
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
    <Card title={title} right={<span className="text-xs text-zinc-500">cases: {metrics.data.cases.join(", ")}</span>}>
      <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] text-left text-sm">
        <thead className="text-xs uppercase text-zinc-500">
          <tr>
            <th className="py-1 pr-3">Metric</th>
            <th className="py-1 pr-3">Baseline</th>
            <th className="py-1">ML</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(({ label, cell }) => (
            <tr key={label} className="border-t border-zinc-800">
              <td className="py-1 pr-3">{label}</td>
              <td className="py-1 pr-3">{baseline ? cell(baseline.totals) : "—"}</td>
              <td className="py-1">{ml ? cell(ml.totals) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <p className="mt-2 text-xs text-zinc-500">
        Top-1/top-5 count only steps where the path so far was correct, so their denominators differ between rankers.
      </p>
    </Card>
  );
}
