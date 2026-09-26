// Types for the JSON the VERDICT API serves (CLAUDE.md §14) and small fetch helpers.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// demo01-demo50: test disks, never used in training. docx01-docx10: DOCX prototype disks.
export const CASES = [
  ...Array.from({ length: 50 }, (_, i) => `demo${String(i + 1).padStart(2, "0")}`),
  ...Array.from({ length: 10 }, (_, i) => `docx${String(i + 1).padStart(2, "0")}`),
];
export type RankerName = "baseline" | "ml";
export type EvidenceState = "PROVEN" | "PLAUSIBLE" | "PARTIAL" | "REJECTED";

export type TrailEvent = {
  step: number;
  event: "START" | "PLACE" | "VERIFY_OK" | "VERIFY_FAIL" | "BACKTRACK" | "STOP";
  block?: number | null;
  rank?: number | null;
  ranked?: number[];
  chunk?: string;
  reason?: string;
};

export type Check = {
  check: string;
  ok: boolean | null; // null = not reached
  stored?: string;
  computed?: string;
  actual?: number | null;
  expected?: number | null;
};

export type Triage = {
  score: number;
  components: { state: number; completeness: number; category: number };
  weights: { state: number; completeness: number; category: number };
  label: string;
};

export type Artifact = {
  id: string;
  type: string;
  category: string;
  anchor_block: number;
  width: number | null; // from the verified IHDR
  height: number | null;
  assembly: number[];
  state: EvidenceState;
  verified_bytes: number;
  expected_bytes: number | null;
  unverifiable_from: number | null;
  sha256: string | null;
  attempts: number;
  explanation: string;
  checks: Check[];
  trail: TrailEvent[];
  triage: Triage;
  text_preview?: string; // DOCX/ZIP only: text of the verified word/document.xml
};

// DOCX and ZIP artifacts show verified text instead of an image preview.
export function isZipLike(a: Artifact): boolean {
  return a.type === "docx" || a.type === "zip";
}

export type CaseReport = {
  case_id: string;
  image_sha256: string;
  ranker: RankerName;
  summary: {
    artifacts: number;
    PROVEN: number;
    PLAUSIBLE: number;
    PARTIAL: number;
    REJECTED: number;
    unattributed_blocks: number;
    duplicate_blocks: number;
  };
  artifacts: Artifact[];
};

export type Ratio = { numerator: number; denominator: number; ratio: number | null };

export type RankerTotals = {
  cases: number;
  artifacts: number;
  state_counts: Record<EvidenceState, number>;
  exact_recovery: Ratio;
  false_verified_count: number;
  known_intact_recovered: Ratio;
  total_attempts: number;
  attempts_per_artifact: number | null;
  partial_prefix_match: Ratio;
  top1: Ratio;
  top5: Ratio;
};

export type Metrics = {
  cases: string[];
  rankers: Partial<Record<RankerName, { totals: RankerTotals }>>;
};

export type Preview = { url: string; rows: number; height: number; note: string };

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function fetchCase(caseId: string, ranker: RankerName): Promise<CaseReport> {
  return getJson<CaseReport>(`/case/${caseId}?ranker=${ranker}`);
}

export function fetchMetrics(): Promise<Metrics> {
  return getJson<Metrics>("/metrics");
}

export function previewUrl(caseId: string, artifactId: string, ranker: RankerName): string {
  return `${API_URL}/artifact/${caseId}/${artifactId}/preview?ranker=${ranker}`;
}

// "PNG image · 183×259" — type and category from classification, size from IHDR.
export function typeLabel(a: Artifact): string {
  if (a.type === "docx") return "DOCX document";
  if (a.type === "zip") return "ZIP archive";
  const size = a.width && a.height ? ` · ${a.width}×${a.height}` : "";
  return `${a.type.toUpperCase()} ${a.category}${size}`;
}

// The preview is fetched as a blob so we can also read the verified-row headers.
export async function fetchPreview(caseId: string, artifactId: string, ranker: RankerName): Promise<Preview> {
  const response = await fetch(previewUrl(caseId, artifactId, ranker), { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `${response.status} ${response.statusText}`);
  }
  const blob = await response.blob();
  return {
    url: URL.createObjectURL(blob),
    rows: Number(response.headers.get("X-Verdict-Rows") ?? 0),
    height: Number(response.headers.get("X-Verdict-Height") ?? 0),
    note: response.headers.get("X-Verdict-Preview") ?? "",
  };
}
