"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Extraction } from "@/lib/api";
import { Badge, Card, ConfidenceBadge, EmptyState, Skeleton } from "@/components/ui";
import { Icons } from "@/components/app-shell";

export default function ReviewQueuePage() {
  const [items, setItems] = useState<Extraction[] | null>(null);

  useEffect(() => {
    api<Extraction[]>("/api/v1/reviews/queue").then(setItems);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Review queue</h1>
        <p className="mt-1 text-sm text-subtle">
          Documents that need a human look, ordered by lowest confidence first.
        </p>
      </div>

      {!items && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      )}

      {items && items.length === 0 && (
        <Card>
          <EmptyState
            icon={<Icons.Check className="h-8 w-8" />}
            title="Nothing needs review"
            description="Every processed document met its confidence threshold and passed validation."
          />
        </Card>
      )}

      {items && items.length > 0 && (
        <div className="space-y-3">
          {items.map((item) => (
            <Link key={item.id} href={`/documents/${item.document_id}`}>
              <Card className="transition-colors hover:border-brand/40">
                <div className="flex items-center justify-between gap-4 px-5 py-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium capitalize text-ink">
                        {item.document_type_key.replace("_", " ")}
                      </span>
                      <Badge tone="neutral">rev. {item.revision}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-subtle">
                      {item.review_reasons.slice(0, 2).join(" · ") || "Flagged for review"}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <div className="text-right">
                      <p className="text-xs text-subtle">Confidence</p>
                      <p className="text-sm font-semibold tabular-nums text-ink">
                        {item.overall_confidence !== null
                          ? `${(item.overall_confidence * 100).toFixed(0)}%`
                          : "—"}
                      </p>
                    </div>
                    <ConfidenceBadge
                      band={
                        item.overall_confidence === null
                          ? null
                          : item.overall_confidence >= 0.85
                            ? "high"
                            : item.overall_confidence >= 0.6
                              ? "medium"
                              : "low"
                      }
                    />
                  </div>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
