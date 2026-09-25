// One row per carved artifact, baseline and ML side by side. Rows where the two
// rankers end in different evidence states are highlighted.
import type { Artifact, CaseReport, RankerName } from "@/lib/api";
import { Card, Empty, ErrorBox, Loading, StateBadge, type Remote } from "./ui";

type Props = {
  baseline: Remote<CaseReport>;
  ml: Remote<CaseReport>;
  active: RankerName;
  selectedAnchor: number | null;
  onSelect: (anchor: number) => void;
};

function byAnchor(report: Remote<CaseReport>): Map<number, Artifact> {
  const map = new Map<number, Artifact>();
  if (report.status === "ok") report.data.artifacts.forEach((a) => map.set(a.anchor_block, a));
  return map;
}

export function ArtifactTable({ baseline, ml, active, selectedAnchor, onSelect }: Props) {
  if (baseline.status === "loading" && ml.status === "loading") {
    return <Card title="Artifacts"><Loading what="artifacts" /></Card>;
  }
  if (baseline.status === "error" && ml.status === "error") {
    return <Card title="Artifacts"><ErrorBox message={baseline.message} /></Card>;
  }

  const base = byAnchor(baseline);
  const learned = byAnchor(ml);
  const anchors = [...new Set([...base.keys(), ...learned.keys()])].sort((a, b) => a - b);
  if (anchors.length === 0) {
    return <Card title="Artifacts"><Empty message="No PNG anchors were carved from this image." /></Card>;
  }

  return (
    <Card title="Artifacts" right={<span className="text-xs text-zinc-500">click a row for the proof</span>}>
      {baseline.status === "error" && <ErrorBox message={`baseline: ${baseline.message}`} />}
      {ml.status === "error" && <ErrorBox message={`ml: ${ml.message}`} />}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-2 pr-3">Anchor block</th>
              <th className="py-2 pr-3">Baseline state</th>
              <th className="py-2 pr-3">ML state</th>
              <th className="py-2 pr-3">Attempts (baseline / ML)</th>
              <th className="py-2 pr-3">Triage ({active === "ml" ? "ML" : "baseline"})</th>
            </tr>
          </thead>
          <tbody>
            {anchors.map((anchor) => {
              const b = base.get(anchor);
              const m = learned.get(anchor);
              const differs = b && m && b.state !== m.state;
              const shown = active === "ml" ? m : b;
              const selected = anchor === selectedAnchor;
              return (
                <tr
                  key={anchor}
                  onClick={() => onSelect(anchor)}
                  className={`cursor-pointer border-t border-zinc-800 hover:bg-zinc-800/60 ${
                    selected ? "bg-zinc-800" : differs ? "bg-indigo-500/10" : ""
                  }`}
                >
                  <td className="py-2 pr-3 font-mono">
                    {anchor}
                    {differs && <span className="ml-2 text-xs text-indigo-300">states differ</span>}
                  </td>
                  <td className="py-2 pr-3"><StateBadge state={b?.state} /></td>
                  <td className="py-2 pr-3"><StateBadge state={m?.state} /></td>
                  <td className="py-2 pr-3 font-mono">{b?.attempts ?? "—"} / {m?.attempts ?? "—"}</td>
                  <td className="py-2 pr-3 font-mono">{shown ? shown.triage.score.toFixed(2) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
