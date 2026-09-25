// Shared UI pieces: state badge, card, segmented control, skeleton / error / empty boxes.
import type { ReactNode } from "react";
import type { EvidenceState } from "@/lib/api";

// Colour is never the only signal: every badge also shows the state word.
export const STATE_TONE: Record<EvidenceState, { text: string; dot: string; bg: string; ring: string }> = {
  PROVEN: { text: "text-proven", dot: "bg-proven", bg: "bg-proven/10", ring: "ring-proven/30" },
  PLAUSIBLE: { text: "text-plausible", dot: "bg-plausible", bg: "bg-plausible/10", ring: "ring-plausible/30" },
  PARTIAL: { text: "text-partial", dot: "bg-partial", bg: "bg-partial/10", ring: "ring-partial/30" },
  REJECTED: { text: "text-rejected", dot: "bg-rejected", bg: "bg-rejected/10", ring: "ring-rejected/30" },
};

export function StateBadge({ state }: { state: EvidenceState | undefined }) {
  if (!state) return <span className="text-muted">—</span>;
  const tone = STATE_TONE[state];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wider ring-1 ${tone.bg} ${tone.text} ${tone.ring}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
      {state}
    </span>
  );
}

export function Card({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="min-w-0 rounded-[var(--radius-card)] border border-line bg-panel p-5 shadow-[var(--shadow-card)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-2">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: [T, string][];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-full border border-line bg-panel-2 p-0.5 text-xs">
      {options.map(([option, text]) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={`rounded-full px-3.5 py-1.5 font-medium transition-colors duration-200 ${
            value === option ? "bg-ink text-bg shadow-sm" : "text-ink-2 hover:text-ink"
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`rounded-lg bg-panel-2 motion-safe:animate-pulse ${className}`} />;
}

export function Loading({ what, lines = 3 }: { what: string; lines?: number }) {
  return (
    <div role="status" aria-label={`Loading ${what}`} className="space-y-2.5">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-4 ${i === lines - 1 ? "w-2/3" : "w-full"}`} />
      ))}
      <span className="sr-only">Loading {what}…</span>
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-xl border border-rejected/40 bg-rejected/10 p-3 text-sm text-ink">
      <span className="font-semibold text-rejected">Error · </span>Could not load: {message}
    </p>
  );
}

export function Empty({ message }: { message: string }) {
  return <p className="text-sm text-ink-2">{message}</p>;
}

// Remote data in one of three states, so every view can show loading / error / data.
export type Remote<T> = { status: "loading" } | { status: "error"; message: string } | { status: "ok"; data: T };
