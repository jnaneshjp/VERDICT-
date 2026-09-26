"use client";
// Everything about one artifact: evidence preview, explanation, Proof Panel, checks, triage.
import { useEffect, useState } from "react";
import { fetchPreview, isZipLike, typeLabel, type Artifact, type Preview, type RankerName } from "@/lib/api";
import { ChecksTable, Timeline } from "./ProofPanel";
import { Card, Empty, ErrorBox, Loading, Skeleton, StateBadge, type Remote } from "./ui";

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
  if (preview.status === "loading") return <Skeleton className="h-56 w-56 rounded-xl" />;
  if (preview.status === "error") return <ErrorBox message={preview.message} />;

  const { url, rows, height, note } = preview.data;
  const partial = rows < height;
  return (
    <figure className="rounded-xl border border-line bg-bg p-3">
      <div className="mb-2 flex items-center justify-between gap-2 text-[10px] uppercase tracking-[0.14em] text-muted">
        <span>Evidence · {artifact.id}</span>
        <span className="font-mono normal-case tracking-normal">{typeLabel(artifact)}</span>
      </div>
      <div className="relative mx-auto w-fit max-w-full">
        {/* eslint-disable-next-line @next/next/no-img-element -- local blob URL */}
        <img
          src={url}
          alt={`preview of ${artifact.id}`}
          className="block max-h-72 max-w-full rounded-md [image-rendering:pixelated]"
        />
        {partial && (
          <div
            className="absolute inset-x-0 border-t-2 border-partial"
            style={{ top: `${(rows / height) * 100}%` }}
            title={`last verified row: ${rows}`}
          />
        )}
      </div>
      <figcaption className="mt-2 text-xs text-ink-2">
        {partial && <span className="mr-1 font-semibold text-partial">Line = last verified row ·</span>}
        {note}
      </figcaption>
    </figure>
  );
}

// DOCX/ZIP: the text of word/document.xml, shown only if that entry's CRC-32 verified.
function PreviewText({ artifact }: { artifact: Artifact }) {
  if (artifact.state === "REJECTED") return <Empty message="No preview: no ZIP entry verified." />;
  return (
    <figure className="w-full max-w-md rounded-xl border border-line bg-bg p-3">
      <div className="mb-2 flex items-center justify-between gap-2 text-[10px] uppercase tracking-[0.14em] text-muted">
        <span>Evidence · {artifact.id}</span>
        <span className="font-mono normal-case tracking-normal">{typeLabel(artifact)}</span>
      </div>
      {artifact.text_preview ? (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-ink">
          {artifact.text_preview}
        </pre>
      ) : (
        <Empty message="word/document.xml is not among the verified entries, so no text is shown." />
      )}
      <figcaption className="mt-2 text-xs text-ink-2">
        Text extracted only from entries whose CRC-32 and size verified.
      </figcaption>
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
      <p className="mb-3 rounded-lg border border-partial/40 bg-partial/10 px-3 py-2 text-xs font-medium text-partial">
        {label}
      </p>
      <table className="w-full">
        <tbody>
          {rows.map(([name, weight, value]) => (
            <tr key={name} className="border-t border-line">
              <td className="py-1.5 pr-3 text-ink-2">{name}</td>
              <td className="py-1.5 pr-3 font-mono text-muted">{weight} ×</td>
              <td className="py-1.5 pr-3 font-mono text-ink">{value}</td>
              <td className="py-1.5 text-right font-mono text-muted">= {(weight * value).toFixed(3)}</td>
            </tr>
          ))}
          <tr className="border-t border-ink-2/40">
            <td className="py-2 font-semibold text-ink" colSpan={3}>score</td>
            <td className="py-2 text-right font-mono text-base font-semibold text-ink">{score.toFixed(3)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

type Props = { caseId: string; ranker: RankerName; report: Remote<{ artifacts: Artifact[] }>; anchor: number | null };

export function DetailPanel({ caseId, ranker, report, anchor }: Props) {
  const title = `Artifact detail · ${ranker === "ml" ? "ML" : "baseline"} ranker`;
  if (report.status === "loading") return <Card title={title}><Loading what="artifact" lines={6} /></Card>;
  if (report.status === "error") return <Card title={title}><ErrorBox message={report.message} /></Card>;
  if (anchor === null) return <Card title={title}><Empty message="Select an artifact row to see its proof." /></Card>;
  const artifact = report.data.artifacts.find((a) => a.anchor_block === anchor);
  if (!artifact) return <Card title={title}><Empty message={`No artifact at anchor ${anchor} for this ranker.`} /></Card>;

  return (
    <div className="space-y-5">
      <Card title={title} right={<StateBadge state={artifact.state} />}>
        <div className="grid gap-5 2xl:grid-cols-[minmax(0,auto)_minmax(0,1fr)]">
          {isZipLike(artifact)
            ? <PreviewText artifact={artifact} />
            : <PreviewImage caseId={caseId} artifact={artifact} ranker={ranker} />}
          <div className="min-w-0 space-y-4 text-sm">
            <p className="leading-relaxed text-ink">{artifact.explanation}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
              <dt className="text-muted">id</dt><dd className="font-mono text-ink-2">{artifact.id}</dd>
              <dt className="text-muted">anchor</dt><dd className="font-mono text-ink-2">{artifact.anchor_block}</dd>
              <dt className="text-muted">assembly</dt>
              <dd className="break-words font-mono text-ink-2">[{artifact.assembly.join(", ")}]</dd>
              <dt className="text-muted">verified bytes</dt>
              <dd className="font-mono text-ink-2">
                {artifact.verified_bytes}{artifact.expected_bytes !== null && ` / ${artifact.expected_bytes}`}
              </dd>
              <dt className="text-muted">sha256</dt><dd className="break-all font-mono text-ink-2">{artifact.sha256 ?? "—"}</dd>
            </dl>
          </div>
        </div>
      </Card>
      <Card title="Proof panel — search trail"><Timeline trail={artifact.trail} /></Card>
      <div className="grid gap-5 xl:grid-cols-2 min-[1400px]:grid-cols-1 2xl:grid-cols-2">
        <Card title="Format checks"><ChecksTable checks={artifact.checks} /></Card>
        <Card title="Triage"><TriageBox artifact={artifact} /></Card>
      </div>
    </div>
  );
}
