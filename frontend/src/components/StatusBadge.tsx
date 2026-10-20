import type { Extraction } from "@/lib/api";

/** Fixed status vocabulary of the Logic flow UI standard. */
export const STATUS: Record<
  Extraction["status"],
  { label: string; className: string }
> = {
  ok: { label: "Ověřeno", className: "bg-ok-soft text-ok" },
  needs_review: { label: "Ke kontrole", className: "bg-warn-soft text-warn" },
  approved: { label: "Schváleno", className: "bg-ok text-white" },
  failed: { label: "Chyba", className: "bg-err-soft text-err" },
  running: { label: "Zpracovává se", className: "bg-info-bg text-info" },
};

export function StatusBadge({ status }: { status: Extraction["status"] }) {
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS[status].className}`}
    >
      {STATUS[status].label}
    </span>
  );
}
