"use client";
// Everything about one artifact: preview, explanation, Proof Panel, checks, triage.
import { useEffect, useState } from "react";
import { fetchPreview, type Artifact, type Preview, type RankerName } from "@/lib/api";
import { ChecksTable, Timeline } from "./ProofPanel";
import { Card, Empty, ErrorBox, Loading, StateBadge, type Remote } from "./ui";

function PreviewImage({ caseId, artifact, ranker }: { caseId: string; artifact: Artifact; ranker: RankerName }) {
  const [preview, setPreview] = useState<Remote<Preview>>({ status: "loading" });

  useEffect(() => {
    if (artifact.state === "REJECTED") return;
    let url: string | null = null;
    let cancelled = false;
    setPreview({ status: "loading" });
    fetchPreview(caseId, artifact.id, ranker)
      .then((p) => {
        url = p.url;
        if (!cancelled) setPreview({ status: "ok", data: p });
      })
      .catch((err: Error) => !cancelled && setPreview({ status: "error", message: err.message }));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [caseId, artifact.id, artifact.state, ranker]);

  if (artifact.state === "REJECTED") return <Empty message="No preview: nothing beyond the signature verified." />;
  if (preview.status === "loading") return <Loading what="preview" />;
  if (preview.status === "error") return <ErrorBox message={preview.message} />;

  const { url, rows, height, note } = preview.data;
  const partial = rows < height;
  return (
    <figure>
      <div className="relative inline-block max-w-full">
        {/* eslint-disable-next-line @next/next/no-img-element -- local blob URL */}
        <img src={url} alt={`preview of ${artifact.id}`} className="block max-h-80 max-w-full [image-rendering:pixelated]" />
        {partial && (
          <div
            className="absolute inset-x-0 border-t-2 border-amber-400"
            style={{ top: `${(rows / height) * 100}%` }}
            title={`last verified row: ${rows}`}
          />
        )}
      </div>
      <figcaption className="mt-2 text-xs text-zinc-400">{note}</figcaption>
    </figure>
  );
}

function TriageBox({ artifact }: { artifact: Artifact }) {
  const { score, components, weights, label } = artifact.triage;
  const rows: [string, number, number][] = [
    ["evidence state", weights.state, components.state],
    ["completeness", weights.completeness, components.completeness],
    ["category", weights.category, components.category],
  ];
  return (
    <div className="text-sm">
      <p className="mb-2 text-xs font-semibold text-amber-300">{label}</p>
      <table className="w-full font-mono">
        <tbody>
          {rows.map(([name, weight, value]) => (
            <tr key={name} className="border-t border-zinc-800">
              <td className="py-1 pr-3 font-sans">{name}</td>
              <td className="py-1 pr-3 text-zinc-400">{weight} ×</td>
              <td className="py-1 pr-3">{value}</td>
              <td className="py-1 text-right text-zinc-400">= {(weight * value).toFixed(3)}</td>
            </tr>
          ))}
          <tr className="border-t border-zinc-600">
            <td className="py-1 font-sans font-semibold" colSpan={3}>score</td>
            <td className="py-1 text-right font-semibold">{score.toFixed(3)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

type Props = { caseId: string; ranker: RankerName; report: Remote<{ artifacts: Artifact[] }>; anchor: number | null };

export function DetailPanel({ caseId, ranker, report, anchor }: Props) {
  const title = `Artifact detail (${ranker === "ml" ? "ML" : "baseline"} ranker)`;
  if (anchor === null) return <Card title={title}><Empty message="Select an artifact row to see its proof." /></Card>;
  if (report.status === "loading") return <Card title={title}><Loading what="artifact" /></Card>;
  if (report.status === "error") return <Card title={title}><ErrorBox message={report.message} /></Card>;
  const artifact = report.data.artifacts.find((a) => a.anchor_block === anchor);
  if (!artifact) return <Card title={title}><Empty message={`No artifact at anchor ${anchor} for this ranker.`} /></Card>;

  return (
    <div className="space-y-4">
      <Card title={title} right={<StateBadge state={artifact.state} />}>
        <div className="grid gap-4 md:grid-cols-[minmax(0,auto)_minmax(0,1fr)]">
          <PreviewImage caseId={caseId} artifact={artifact} ranker={ranker} />
          <div className="space-y-2 text-sm">
            <p className="leading-relaxed text-zinc-200">{artifact.explanation}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-xs text-zinc-400">
              <dt>id</dt><dd>{artifact.id}</dd>
              <dt>assembly</dt><dd className="break-words">[{artifact.assembly.join(", ")}]</dd>
              <dt>verified bytes</dt>
              <dd>{artifact.verified_bytes}{artifact.expected_bytes !== null && ` / ${artifact.expected_bytes}`}</dd>
              <dt>sha256</dt><dd className="break-all">{artifact.sha256 ?? "—"}</dd>
            </dl>
          </div>
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Proof panel — search trail"><Timeline trail={artifact.trail} /></Card>
        <div className="min-w-0 space-y-4">
          <Card title="Format checks"><ChecksTable checks={artifact.checks} /></Card>
          <Card title="Triage"><TriageBox artifact={artifact} /></Card>
        </div>
      </div>
    </div>
  );
}
