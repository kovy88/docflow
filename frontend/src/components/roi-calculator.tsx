"use client";

/**
 * ROI calculator: the sales/interview artifact from the spec.
 *
 * The math is deliberately simple — three inputs, one formula — because the
 * point is to give a prospect a number they can sanity-check in their head, not
 * to look sophisticated. `currentCostPerDocument` is threaded in from the
 * dashboard's real usage data when available; everything else is a user-editable
 * assumption, and is labelled as such rather than presented as measured.
 */

import { useMemo, useState } from "react";
import { Card, CardBody, CardHeader, CardTitle, Input, Label } from "@/components/ui";
import { formatCurrency } from "@/lib/utils";

export function RoiCalculator({ currentCostPerDocument }: { currentCostPerDocument?: number }) {
  const [documentsPerMonth, setDocumentsPerMonth] = useState(500);
  const [minutesPerDocument, setMinutesPerDocument] = useState(6);
  const [hourlyCost, setHourlyCost] = useState(25);
  const [aiCostPerDocument, setAiCostPerDocument] = useState(
    currentCostPerDocument && currentCostPerDocument > 0 ? currentCostPerDocument : 0.08,
  );

  const result = useMemo(() => {
    const manualHours = (documentsPerMonth * minutesPerDocument) / 60;
    const manualCost = manualHours * hourlyCost;
    const aiCost = documentsPerMonth * aiCostPerDocument;
    // A human still reviews the ~8-15% the system flags; modelled as 10% of the
    // original per-document time rather than assumed away.
    const reviewHours = manualHours * 0.1;
    const reviewCost = reviewHours * hourlyCost;
    const netSavings = manualCost - (aiCost + reviewCost);
    const hoursSaved = manualHours - reviewHours;
    return { manualCost, aiCost, reviewCost, netSavings, hoursSaved, manualHours };
  }, [documentsPerMonth, minutesPerDocument, hourlyCost, aiCostPerDocument]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>ROI calculator</CardTitle>
        <span className="text-xs text-subtle">Adjust the assumptions for your team</span>
      </CardHeader>
      <CardBody>
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-4">
            <Field
              label="Documents processed per month"
              value={documentsPerMonth}
              onChange={setDocumentsPerMonth}
              min={1}
            />
            <Field
              label="Minutes to process manually"
              value={minutesPerDocument}
              onChange={setMinutesPerDocument}
              min={1}
              step={0.5}
            />
            <Field
              label="Fully-loaded hourly cost ($)"
              value={hourlyCost}
              onChange={setHourlyCost}
              min={1}
            />
            <Field
              label="AI cost per document ($)"
              value={aiCostPerDocument}
              onChange={setAiCostPerDocument}
              min={0}
              step={0.01}
              hint={
                currentCostPerDocument && currentCostPerDocument > 0
                  ? "Pre-filled from your measured usage"
                  : "Estimate — see docs/EVALUATION.md for measured figures"
              }
            />
          </div>

          <div className="rounded-lg bg-muted p-5">
            <dl className="space-y-3">
              <Row label="Manual processing cost" value={formatCurrency(result.manualCost)} />
              <Row label="AI processing cost" value={formatCurrency(result.aiCost)} />
              <Row label="Human review cost (~10% of docs)" value={formatCurrency(result.reviewCost)} />
              <div className="my-2 border-t border-line" />
              <Row
                label="Estimated monthly savings"
                value={formatCurrency(result.netSavings)}
                emphasis
              />
              <Row label="Hours freed up per month" value={result.hoursSaved.toFixed(0)} />
            </dl>
          </div>
        </div>
        <p className="mt-4 text-xs text-subtle">
          This is a planning estimate based on the assumptions above, not a guarantee.
          Manual-processing time and AI cost per document vary by document type and quality.
        </p>
      </CardBody>
    </Card>
  );
}

function Field({
  label,
  value,
  onChange,
  min,
  step = 1,
  hint,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  step?: number;
  hint?: string;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <Input
        type="number"
        value={value}
        min={min}
        step={step}
        onChange={(e) => onChange(Number(e.target.value) || 0)}
      />
      {hint && <p className="mt-1 text-xs text-subtle">{hint}</p>}
    </div>
  );
}

function Row({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-sm text-subtle">{label}</dt>
      <dd className={emphasis ? "text-lg font-semibold text-ok" : "text-sm font-medium text-ink"}>
        {value}
      </dd>
    </div>
  );
}
