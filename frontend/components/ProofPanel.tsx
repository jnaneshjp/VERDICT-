// Proof Panel: the search trail as a vertical timeline, plus the format checks.
import type { Check, TrailEvent } from "@/lib/api";

const REASON: Record<string, string> = {
  chunk_crc_mismatch: "CRC mismatch",
  chunk_type_invalid: "next chunk header is not a valid chunk type",
  chunk_length_too_large: "next chunk declares an impossible length",
  zlib_error: "image data did not inflate",
  inflated_length_mismatch: "inflated size differs from IHDR",
  idat_not_consecutive: "IDAT chunks not consecutive",
};

const TONE = {
  neutral: { text: "text-zinc-300", dot: "bg-zinc-500" },
  place: { text: "text-zinc-200", dot: "bg-sky-500" },
  ok: { text: "text-emerald-300", dot: "bg-emerald-500" },
  fail: { text: "text-red-300", dot: "bg-red-500" },
  backtrack: { text: "text-amber-300", dot: "bg-amber-500" },
};

function describe(e: TrailEvent): { text: string; tone: keyof typeof TONE } {
  switch (e.event) {
    case "START":
      return { text: `start at anchor block ${e.block}`, tone: "neutral" };
    case "PLACE":
      return { text: `tried block ${e.block} (rank #${e.rank ?? "?"})`, tone: "place" };
    case "VERIFY_OK":
      return e.chunk === "final_checks"
        ? { text: "image data inflated and matched IHDR size", tone: "ok" }
        : { text: `${e.chunk}: CRC matched`, tone: "ok" };
    case "VERIFY_FAIL":
      return { text: `${e.chunk}: ${REASON[e.reason ?? ""] ?? e.reason}`, tone: "fail" };
    case "BACKTRACK":
      return { text: `backtracked (removed block ${e.block})`, tone: "backtrack" };
    case "STOP":
      return { text: `search stopped: ${e.reason}`, tone: "neutral" };
  }
}

export function Timeline({ trail }: { trail: TrailEvent[] }) {
  if (trail.length === 0) return <p className="text-sm text-zinc-500">No search events recorded.</p>;
  return (
    <ol className="max-h-96 overflow-y-auto border-l border-zinc-700 pl-4 text-sm">
      {trail.map((e, i) => {
        const { text, tone } = describe(e);
        return (
          <li key={i} className="relative py-1">
            <span className={`absolute -left-[21px] top-2.5 h-2 w-2 rounded-full ${TONE[tone].dot}`} />
            <span className="mr-2 font-mono text-xs text-zinc-500">step {e.step}</span>
            <span className={TONE[tone].text}>{text}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Mark({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="text-zinc-500">not reached</span>;
  return ok ? <span className="text-emerald-400">✓ pass</span> : <span className="text-red-400">✗ fail</span>;
}

export function ChecksTable({ checks }: { checks: Check[] }) {
  if (checks.length === 0) return <p className="text-sm text-zinc-500">No checks recorded.</p>;
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs uppercase text-zinc-500">
        <tr>
          <th className="py-1 pr-3">Check</th>
          <th className="py-1 pr-3">Stored</th>
          <th className="py-1 pr-3">Computed</th>
          <th className="py-1">Result</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {checks.map((c) => (
          <tr key={c.check} className="border-t border-zinc-800">
            <td className="py-1 pr-3">{c.check}</td>
            <td className="py-1 pr-3">{c.stored ?? (c.expected != null ? c.expected : "")}</td>
            <td className="py-1 pr-3">{c.computed ?? (c.actual != null ? c.actual : "")}</td>
            <td className="py-1"><Mark ok={c.ok} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
