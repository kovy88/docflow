"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { analytics, type Dashboard } from "@/lib/api";
import { formatCurrency, formatDuration, titleCase } from "@/lib/utils";
import { Card, CardBody, Skeleton, Button } from "@/components/ui";
import { RoiCalculator } from "@/components/roi-calculator";

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    analytics
      .dashboard()
      .then(setData)
      .catch(() => setError("Could not load dashboard metrics."));
  }, []);

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Dashboard</h1>
          <p className="mt-1 text-sm text-subtle">Processing activity and cost for this billing period.</p>
        </div>
        <Link href="/documents">
          <Button>Upload document</Button>
        </Link>
      </div>

      {error && <div className="rounded-lg bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>}

      {!data && !error && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard label="Total documents" value={String(data.total_documents)} />
            <StatCard
              label="Needs review"
              value={String(data.needs_review)}
              tone={data.needs_review > 0 ? "warn" : undefined}
              href="/review"
            />
            <StatCard
              label="Success rate"
              value={data.success_rate !== null ? `${(data.success_rate * 100).toFixed(0)}%` : "—"}
              hint="of terminal documents"
            />
            <StatCard
              label="Review rate"
              value={data.review_rate !== null ? `${(data.review_rate * 100).toFixed(0)}%` : "—"}
              hint="needed a human"
            />
            <StatCard
              label="This period"
              value={`${data.processed_this_period} / ${data.quota === 0 ? "∞" : data.quota}`}
              hint="documents · quota"
            />
            <StatCard
              label="AI cost (period)"
              value={formatCurrency(data.cost_usd_this_period)}
              hint={`priced as of ${data.pricing_as_of}`}
            />
            <StatCard
              label="Cost / document"
              value={data.cost_per_document !== null ? formatCurrency(data.cost_per_document) : "—"}
            />
            <StatCard
              label="Avg. processing time"
              value={data.avg_processing_ms !== null ? formatDuration(data.avg_processing_ms) : "—"}
            />
          </div>

          <Card>
            <CardBody>
              <h3 className="mb-4 text-sm font-semibold text-ink">Documents by status</h3>
              <div className="space-y-2.5">
                {Object.entries(data.by_status).length === 0 && (
                  <p className="text-sm text-subtle">No documents yet. Upload one to get started.</p>
                )}
                {Object.entries(data.by_status)
                  .sort(([, a], [, b]) => b - a)
                  .map(([status, count]) => (
                    <div key={status} className="flex items-center gap-3">
                      <div className="w-32 shrink-0 text-sm text-subtle">{titleCase(status)}</div>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-brand"
                          style={{
                            width: `${(count / Math.max(1, data.total_documents)) * 100}%`,
                          }}
                        />
                      </div>
                      <div className="w-8 shrink-0 text-right text-sm font-medium text-ink">
                        {count}
                      </div>
                    </div>
                  ))}
              </div>
            </CardBody>
          </Card>
        </>
      )}

      <RoiCalculator currentCostPerDocument={data?.cost_per_document ?? undefined} />
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  tone,
  href,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "warn";
  href?: string;
}) {
  const content = (
    <Card className={tone === "warn" && value !== "0" ? "border-warn/40" : undefined}>
      <CardBody className="p-4">
        <p className="text-xs font-medium text-subtle">{label}</p>
        <p className="mt-1.5 text-2xl font-semibold tabular-nums text-ink">{value}</p>
        {hint && <p className="mt-1 text-xs text-subtle">{hint}</p>}
      </CardBody>
    </Card>
  );
  return href ? <Link href={href}>{content}</Link> : content;
}
