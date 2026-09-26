// Proof Panel: the search trail as a vertical timeline, plus the format checks.
// Every colour is paired with a word, so meaning never depends on colour alone.
import type { ReactNode } from "react";
import type { Check, TrailEvent } from "@/lib/api";

const REASON: Record<string, string> = {
  chunk_crc_mismatch: "CRC mismatch",
  chunk_type_invalid: "next chunk header is not a valid chunk type",
  chunk_length_too_large: "next chunk declares an impossible length",
  zlib_error: "image data did not inflate",
  inflated_length_mismatch: "inflated size differs from IHDR",
  idat_not_consecutive: "IDAT chunks not consecutive",
  entry_zlib_error: "compressed entry did not inflate",
  entry_crc_mismatch: "CRC-32 of the entry did not match",
  entry_size_mismatch: "entry size differs from its header",
  central_directory_mismatch: "central directory disagrees with the entries",
  eocd_mismatch: "end-of-central-directory record disagrees",
  unexpected_signature: "next bytes are not a ZIP record",
  local_header_invalid: "next entry header is not plausible",
  unsupported_zip_feature: "unsupported ZIP feature (data descriptor, ZIP64 or encryption)",
};

const TONE = {
  neutral: { word: "text-ink-2", dot: "bg-muted" },
  place: { word: "text-plausible", dot: "bg-plausible" },
  ok: { word: "text-proven", dot: "bg-proven" },
  fail: { word: "text-rejected", dot: "bg-rejected" },
  backtrack: { word: "text-partial", dot: "bg-partial" },
};

const Mono = ({ children }: { children: ReactNode }) => <span className="font-mono text-ink">{children}</span>;

function describe(e: TrailEvent): { word: string; detail: ReactNode; tone: keyof typeof TONE } {
  switch (e.event) {
    case "START":
      return { word: "START", detail: <>anchor block <Mono>{e.block}</Mono></>, tone: "neutral" };
    case "PLACE":
      return { word: "PLACE", detail: <>tried block <Mono>{e.block}</Mono> (rank <Mono>#{e.rank ?? "?"}</Mono>)</>, tone: "place" };
    case "VERIFY_OK":
      if (e.chunk === "zip_final_checks")
        return { word: "VERIFY OK", detail: "central directory and EOCD matched every entry", tone: "ok" };
      return e.chunk === "final_checks"
        ? { word: "VERIFY OK", detail: "image data inflated and matched IHDR size", tone: "ok" }
        : { word: "VERIFY OK", detail: <><Mono>{e.chunk}</Mono>: CRC matched</>, tone: "ok" };
    case "VERIFY_FAIL":
      return { word: "VERIFY FAIL", detail: <><Mono>{e.chunk}</Mono>: {REASON[e.reason ?? ""] ?? e.reason}</>, tone: "fail" };
    case "BACKTRACK":
      return { word: "BACKTRACK", detail: <>backtracked (removed block <Mono>{e.block}</Mono>)</>, tone: "backtrack" };
    case "STOP":
      return { word: "STOP", detail: <>search stopped: <Mono>{e.reason}</Mono></>, tone: "neutral" };
  }
}

export function Timeline({ trail }: { trail: TrailEvent[] }) {
  if (trail.length === 0) return <p className="text-sm text-ink-2">No search events recorded.</p>;
  return (
    <ol className="relative max-h-[28rem] overflow-y-auto pr-2 text-sm" aria-label="Search trail">
      <span aria-hidden className="absolute bottom-3 left-[5px] top-3 w-px bg-line" />
      {trail.map((e, i) => {
        const { word, detail, tone } = describe(e);
        return (
          <li key={i} className="relative grid grid-cols-[12px_auto_1fr] items-baseline gap-x-3 py-1.5">
            <span aria-hidden className={`relative top-[3px] h-[11px] w-[11px] rounded-full ring-4 ring-panel ${TONE[tone].dot}`} />
            <span className="whitespace-nowrap font-mono text-[11px] text-muted">
              step {e.step}{" "}
              <span className={`font-semibold tracking-wider ${TONE[tone].word}`}>{word}</span>
            </span>
            <span className="min-w-0 text-ink-2">{detail}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Mark({ ok }: { ok: boolean | null }) {
  if (ok === null) {
    return <span className="inline-flex items-center gap-1.5 text-muted"><span aria-hidden>–</span>not reached</span>;
  }
  return ok ? (
    <span className="inline-flex items-center gap-1.5 text-proven"><span aria-hidden>✓</span>pass</span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-rejected"><span aria-hidden>✗</span>fail</span>
  );
}

export function ChecksTable({ checks }: { checks: Check[] }) {
  if (checks.length === 0) return <p className="text-sm text-ink-2">No checks recorded.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[420px] text-left text-sm">
        <thead className="text-[11px] uppercase tracking-[0.12em] text-muted">
          <tr>
            <th className="pb-2 pr-3 font-medium">Check</th>
            <th className="pb-2 pr-3 font-medium">Stored</th>
            <th className="pb-2 pr-3 font-medium">Computed</th>
            <th className="pb-2 font-medium">Result</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((c) => (
            <tr key={c.check} className="border-t border-line">
              <td className="py-1.5 pr-3 font-mono text-ink">{c.check}</td>
              <td className="py-1.5 pr-3 font-mono text-ink-2">{c.stored ?? (c.expected != null ? c.expected : "")}</td>
              <td className="py-1.5 pr-3 font-mono text-ink-2">{c.computed ?? (c.actual != null ? c.actual : "")}</td>
              <td className="py-1.5 font-medium"><Mark ok={c.ok} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
