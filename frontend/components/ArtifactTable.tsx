"use client";
// One row per carved artifact, baseline and ML side by side. Rows where the two
// rankers end in different evidence states are marked, and flash briefly when
// the ranker toggle flips. Rows can be shown in disk order or by triage score.
import { useEffect, useRef, useState } from "react";
import { previewUrl, typeLabel, type Artifact, type CaseReport, type RankerName } from "@/lib/api";
import { Card, Empty, ErrorBox, Segmented, Skeleton, StateBadge, type Remote } from "./ui";

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

export function byAnchor(report: Remote<CaseReport>): Map<number, Artifact> {
  const map = new Map<number, Artifact>();
  if (report.status === "ok") report.data.artifacts.forEach((a) => map.set(a.anchor_block, a));
  return map;
}

function Thumbnail({ caseId, artifact, ranker }: { caseId: string; artifact: Artifact | undefined; ranker: RankerName }) {
  const [failed, setFailed] = useState(false);
  const box = "flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-bg";
  if (!artifact || artifact.state === "REJECTED" || failed) {
    return <div className={`${box} text-xs text-muted`}>none</div>;
  }
  return (
    <div className={box}>
      {/* eslint-disable-next-line @next/next/no-img-element -- served by the local API */}
      <img
        src={previewUrl(caseId, artifact.id, ranker)}
        alt={`thumbnail of ${artifact.id}`}
        loading="lazy"
        onError={() => setFailed(true)}
        className="max-h-14 max-w-14 object-contain"
      />
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="space-y-3" role="status" aria-label="Loading artifacts">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-14 w-14 rounded-xl" />
          <Skeleton className="h-4 flex-1" />
        </div>
      ))}
    </div>
  );
}

// Count toggle flips so rows whose state differs can replay their highlight.
function useFlashOnChange(active: RankerName): number {
  const [flashId, setFlashId] = useState(0);
  const previous = useRef(active);
  useEffect(() => {
    if (previous.current !== active) {
      previous.current = active;
      setFlashId((id) => id + 1);
    }
  }, [active]);
  return flashId;
}

export function ArtifactTable({ caseId, baseline, ml, active, order, onOrderChange, selectedAnchor, onSelect }: Props) {
  const flashId = useFlashOnChange(active);
  const toggle = (
    <Segmented
      label="Row order"
      options={[["disk", "Disk order"], ["priority", "Investigative priority"]]}
      value={order}
      onChange={onOrderChange}
    />
  );

  if (baseline.status === "loading" && ml.status === "loading") {
    return <Card title="Artifacts" right={toggle}><TableSkeleton /></Card>;
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
    <Card title="Artifacts" right={toggle}>
      {baseline.status === "error" && <ErrorBox message={`baseline: ${baseline.message}`} />}
      {ml.status === "error" && <ErrorBox message={`ml: ${ml.message}`} />}
      <p className="mb-3 text-xs text-ink-2">
        {order === "priority"
          ? `Sorted by ${rankerName} triage score (a heuristic, not a conclusion). Select a row for the proof.`
          : "In the order the files start on disk. Select a row for the proof."}
      </p>
      <div className="-mx-2 overflow-x-auto">
        <table className="w-full min-w-[720px] border-separate border-spacing-0 text-left text-sm">
          <thead className="text-[11px] uppercase tracking-[0.12em] text-muted">
            <tr>
              <th className="px-2 pb-2 font-medium">Preview</th>
              <th className="px-2 pb-2 font-medium">Anchor</th>
              <th className="px-2 pb-2 font-medium">Type</th>
              <th className="px-2 pb-2 font-medium">Baseline</th>
              <th className="px-2 pb-2 font-medium">ML</th>
              <th className="px-2 pb-2 font-medium">Attempts B / ML</th>
              <th className="px-2 pb-2 font-medium">Triage ({rankerName})</th>
            </tr>
          </thead>
          <tbody>
            {anchors.map((anchor) => {
              const b = base.get(anchor);
              const m = learned.get(anchor);
              const differs = Boolean(b && m && b.state !== m.state);
              const shown = shownFor(anchor);
              const described = shown ?? b ?? m;
              const selected = anchor === selectedAnchor;
              const cell = "border-t border-line px-2 py-2.5 align-middle";
              return (
                <tr
                  key={differs ? `${anchor}-${flashId}` : anchor}
                  tabIndex={0}
                  aria-selected={selected}
                  onClick={() => onSelect(anchor)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(anchor);
                    }
                  }}
                  className={`group cursor-pointer transition-colors duration-150 ${
                    selected ? "bg-panel-2" : "hover:bg-panel-2/70"
                  } ${differs && flashId > 0 ? "row-flash" : ""}`}
                >
                  <td className={`${cell} relative pl-3`}>
                    <span
                      aria-hidden
                      className={`absolute inset-y-2 left-0 w-[3px] rounded-full transition-colors duration-200 ${
                        selected ? "bg-accent" : "bg-transparent"
                      }`}
                    />
                    <Thumbnail key={`${caseId}-${active}-${anchor}`} caseId={caseId} artifact={shown} ranker={active} />
                  </td>
                  <td className={cell}>
                    <div className="font-mono text-ink">{anchor}</div>
                    {differs && (
                      <div className="mt-1 inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-proven">
                        states differ
                      </div>
                    )}
                  </td>
                  <td className={`${cell} whitespace-nowrap text-ink-2`}>{described ? typeLabel(described) : "—"}</td>
                  <td className={cell}><StateBadge state={b?.state} /></td>
                  <td className={cell}><StateBadge state={m?.state} /></td>
                  <td className={`${cell} font-mono text-ink-2`}>{b?.attempts ?? "—"} / {m?.attempts ?? "—"}</td>
                  <td className={`${cell} font-mono text-ink`}>{shown ? shown.triage.score.toFixed(2) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
