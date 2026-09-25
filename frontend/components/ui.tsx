// Small shared UI pieces: state badge, section card, loading / error / empty boxes.
import type { ReactNode } from "react";
import type { EvidenceState } from "@/lib/api";

const BADGE: Record<EvidenceState, string> = {
  PROVEN: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  PLAUSIBLE: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
  PARTIAL: "bg-amber-500/15 text-amber-300 ring-amber-500/40",
  REJECTED: "bg-red-500/15 text-red-300 ring-red-500/40",
};

export function StateBadge({ state }: { state: EvidenceState | undefined }) {
  if (!state) return <span className="text-zinc-500">—</span>;
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ring-1 ${BADGE[state]}`}>
      {state}
    </span>
  );
}

export function Card({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="animate-pulse text-sm text-zinc-400">Loading {what}…</p>;
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <p className="rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">
      Could not load: {message}
    </p>
  );
}

export function Empty({ message }: { message: string }) {
  return <p className="text-sm text-zinc-500">{message}</p>;
}

// Remote data in one of three states, so every view can show loading / error / data.
export type Remote<T> = { status: "loading" } | { status: "error"; message: string } | { status: "ok"; data: T };
