"use client";
// One row per carved artifact, baseline and ML side by side. Rows where the two
// rankers end in different evidence states are highlighted. Rows can be shown
// in disk order or sorted by the active ranker's triage score.
import { useState } from "react";
import { previewUrl, typeLabel, type Artifact, type CaseReport, type RankerName } from "@/lib/api";
import { Card, Empty, ErrorBox, Loading, StateBadge, type Remote } from "./ui";

export type RowOrder = "disk" | "priority";

type Props = {
  caseId: string;
  baseline: Remote<CaseReport>;
  ml: Remote<CaseReport>;
  active: RankerName;
  order: RowOrder;
  onOrderChange: (order: RowOrder) => void;
  selectedAnchor: number | null;
  onSelect: (anchor: number) => void;
};

function byAnchor(report: Remote<CaseReport>): Map<number, Artifact> {
  const map = new Map<number, Artifact>();
  if (report.status === "ok") report.data.artifacts.forEach((a) => map.set(a.anchor_block, a));
  return map;
}

function Thumbnail({ caseId, artifact, ranker }: { caseId: string; artifact: Artifact | undefined; ranker: RankerName }) {
  const [failed, setFailed] = useState(false);
  const box = "flex h-10 w-10 items-center justify-center rounded bg-zinc-950 ring-1 ring-zinc-800";
  if (!artifact || artifact.state === "REJECTED" || failed) {
    return <div className={`${box} text-xs text-zinc-600`}>—</div>;
  }
  return (
    <div className={box}>
      {/* eslint-disable-next-line @next/next/no-img-element -- served by the local API */}
      <img
        src={previewUrl(caseId, artifact.id, ranker)}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className="max-h-10 max-w-10 object-contain"
      />
    </div>
  );
}

function OrderToggle({ order, onChange }: { order: RowOrder; onChange: (order: RowOrder) => void }) {
  const options: [RowOrder, string][] = [["disk", "Disk order"], ["priority", "Investigative priority"]];
  return (
    <div className="flex overflow-hidden rounded border border-zinc-700 text-xs">
      {options.map(([value, label]) => (
        <button
          key={value}
          onClick={() => onChange(value)}
          className={`px-2 py-1 ${order === value ? "bg-zinc-100 text-zinc-900" : "bg-zinc-900 text-zinc-300 hover:bg-zinc-800"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function ArtifactTable({ caseId, baseline, ml, active, order, onOrderChange, selectedAnchor, onSelect }: Props) {
  if (baseline.status === "loading" && ml.status === "loading") {
    return <Card title="Artifacts"><Loading what="artifacts" /></Card>;
  }
  if (baseline.status === "error" && ml.status === "error") {
    return <Card title="Artifacts"><ErrorBox message={baseline.message} /></Card>;
  }

  const base = byAnchor(baseline);
  const learned = byAnchor(ml);
  const shownFor = (anchor: number) => (active === "ml" ? learned : base).get(anchor);
  const anchors = [...new Set([...base.keys(), ...learned.keys()])].sort((a, b) => a - b);
  if (order === "priority") {
    // Highest triage score first; ties keep disk order.
    anchors.sort((a, b) => (shownFor(b)?.triage.score ?? -1) - (shownFor(a)?.triage.score ?? -1) || a - b);
  }
  if (anchors.length === 0) {
    return <Card title="Artifacts"><Empty message="No PNG anchors were carved from this image." /></Card>;
  }

  const rankerName = active === "ml" ? "ML" : "baseline";
  return (
    <Card title="Artifacts" right={<OrderToggle order={order} onChange={onOrderChange} />}>
      {baseline.status === "error" && <ErrorBox message={`baseline: ${baseline.message}`} />}
      {ml.status === "error" && <ErrorBox message={`ml: ${ml.message}`} />}
      <p className="mb-2 text-xs text-zinc-500">
        {order === "priority"
          ? `Sorted by ${rankerName} triage score (a heuristic, not a conclusion). Click a row for the proof.`
          : "In the order the files start on disk. Click a row for the proof."}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-2 pr-3">Preview</th>
              <th className="py-2 pr-3">Anchor</th>
              <th className="py-2 pr-3">Type</th>
              <th className="py-2 pr-3">Baseline</th>
              <th className="py-2 pr-3">ML</th>
              <th className="py-2 pr-3">Attempts (B / ML)</th>
              <th className="py-2 pr-3">Triage ({rankerName})</th>
            </tr>
          </thead>
          <tbody>
            {anchors.map((anchor) => {
              const b = base.get(anchor);
              const m = learned.get(anchor);
              const differs = b && m && b.state !== m.state;
              const shown = shownFor(anchor);
              const described = shown ?? b ?? m;
              const selected = anchor === selectedAnchor;
              return (
                <tr
                  key={anchor}
                  onClick={() => onSelect(anchor)}
                  className={`cursor-pointer border-t border-zinc-800 hover:bg-zinc-800/60 ${
                    selected ? "bg-zinc-800" : differs ? "bg-indigo-500/10" : ""
                  }`}
                >
                  <td className="py-1.5 pr-3">
                    <Thumbnail key={`${caseId}-${active}-${anchor}`} caseId={caseId} artifact={shown} ranker={active} />
                  </td>
                  <td className="py-1.5 pr-3 font-mono">
                    {anchor}
                    {differs && <span className="ml-2 whitespace-nowrap text-xs text-indigo-300">states differ</span>}
                  </td>
                  <td className="whitespace-nowrap py-1.5 pr-3 text-zinc-300">{described ? typeLabel(described) : "—"}</td>
                  <td className="py-1.5 pr-3"><StateBadge state={b?.state} /></td>
                  <td className="py-1.5 pr-3"><StateBadge state={m?.state} /></td>
                  <td className="py-1.5 pr-3 font-mono">{b?.attempts ?? "—"} / {m?.attempts ?? "—"}</td>
                  <td className="py-1.5 pr-3 font-mono">{shown ? shown.triage.score.toFixed(2) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
