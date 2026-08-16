"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  documents,
  API_URL,
  tokens,
  ApiError,
  type DocumentSummary,
  type Extraction,
  type ProcessingStep,
} from "@/lib/api";
import { formatBytes, formatDateTime, formatDuration } from "@/lib/utils";
import { Button, Card, CardBody, CardHeader, CardTitle, Skeleton, Spinner, StatusBadge } from "@/components/ui";
import { ProcessingTimeline } from "@/components/processing-timeline";
import { ExtractionPanel } from "@/components/extraction-panel";
import { useToast } from "@/components/toast-context";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const documentId = params.id;
  const notify = useToast();

  const [doc, setDoc] = useState<DocumentSummary | null>(null);
  const [steps, setSteps] = useState<ProcessingStep[]>([]);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [loading, setLoading] = useState(true);
  const [reprocessing, setReprocessing] = useState(false);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(async () => {
    try {
      const [docResult, stepsResult] = await Promise.all([
        documents.get(documentId),
        documents.timeline(documentId),
      ]);
      setDoc(docResult);
      setSteps(stepsResult);

      if (docResult.status === "needs_review" || docResult.status === "completed") {
        try {
          setExtraction(await documents.extraction(documentId));
        } catch {
          setExtraction(null);
        }
      } else {
        setExtraction(null);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setNotFound(true);
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!doc || doc.status === "queued" || doc.status === "processing") {
      const timer = setInterval(load, 2000);
      return () => clearInterval(timer);
    }
  }, [doc, load]);

  async function reprocess() {
    setReprocessing(true);
    try {
      await documents.reprocess(documentId);
      notify("brand", "Reprocessing started.");
      await load();
    } catch (err) {
      notify("danger", err instanceof ApiError ? err.message : "Could not reprocess.");
    } finally {
      setReprocessing(false);
    }
  }

  if (notFound) {
    return (
      <div className="py-16 text-center">
        <p className="text-sm text-subtle">Document not found.</p>
        <Link href="/documents" className="mt-2 inline-block text-sm text-brand hover:underline">
          Back to documents
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/documents" className="text-xs text-subtle hover:text-ink">
            ← Documents
          </Link>
          {loading ? (
            <Skeleton className="mt-1 h-7 w-64" />
          ) : (
            <h1 className="mt-1 text-xl font-semibold text-ink">{doc?.filename}</h1>
          )}
          {doc && (
            <div className="mt-1.5 flex items-center gap-3 text-sm text-subtle">
              <StatusBadge status={doc.status} />
              <span>{formatBytes(doc.size_bytes)}</span>
              {doc.page_count && <span>{doc.page_count} page{doc.page_count === 1 ? "" : "s"}</span>}
              {doc.used_ocr && <span>OCR</span>}
              <span>{formatDateTime(doc.created_at)}</span>
            </div>
          )}
        </div>

        {doc && (
          <div className="flex shrink-0 gap-2">
            <a href={`${API_URL}/api/v1/documents/${documentId}/download`} target="_blank" rel="noreferrer">
              <Button
                variant="outline"
                size="sm"
                onClick={async (e) => {
                  e.preventDefault();
                  const resp = await fetch(`${API_URL}/api/v1/documents/${documentId}/download`, {
                    headers: { Authorization: `Bearer ${tokens.access}` },
                  });
                  const { url } = await resp.json();
                  window.open(url, "_blank");
                }}
              >
                Download
              </Button>
            </a>
            <Button variant="outline" size="sm" loading={reprocessing} onClick={reprocess}>
              Reprocess
            </Button>
          </div>
        )}
      </div>

      {doc?.status === "failed" && (
        <Card className="border-danger/30 bg-danger/5">
          <CardBody>
            <p className="text-sm font-medium text-danger">Processing failed</p>
            <p className="mt-1 text-sm text-ink">
              {doc.error_code ? `[${doc.error_code}] ` : ""}
              This document could not be processed. You can try reprocessing it.
            </p>
          </CardBody>
        </Card>
      )}

      {(doc?.status === "queued" || doc?.status === "processing") && (
        <Card className="border-brand/30 bg-brand/5">
          <CardBody className="flex items-center gap-3">
            <Spinner className="h-4 w-4 text-brand" />
            <p className="text-sm text-ink">
              {doc.status === "queued" ? "Waiting in the processing queue…" : "Processing this document…"}
            </p>
          </CardBody>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Processing timeline</CardTitle>
            </CardHeader>
            <CardBody>
              <ProcessingTimeline steps={steps} />
            </CardBody>
          </Card>

          {extraction && (
            <Card>
              <CardHeader>
                <CardTitle>Provenance</CardTitle>
              </CardHeader>
              <CardBody className="space-y-2 text-xs">
                <ProvenanceRow label="Extractor" value={extraction.extractor} />
                <ProvenanceRow label="Provider" value={extraction.provider ?? "—"} />
                <ProvenanceRow label="Model" value={extraction.model ?? "—"} />
                <ProvenanceRow label="Prompt" value={extraction.prompt_version ?? "—"} />
                <ProvenanceRow label="Schema" value={`v${extraction.schema_version}`} />
                <ProvenanceRow label="Tokens in/out" value={`${extraction.input_tokens} / ${extraction.output_tokens}`} />
                <ProvenanceRow label="Cost" value={`$${extraction.cost_usd.toFixed(4)}`} />
                <ProvenanceRow label="Latency" value={formatDuration(extraction.latency_ms)} />
              </CardBody>
            </Card>
          )}
        </div>

        <div>
          {!loading && !extraction && doc && doc.status !== "queued" && doc.status !== "processing" && doc.status !== "failed" && (
            <Card>
              <CardBody>
                <p className="text-sm text-subtle">No extraction available for this document.</p>
              </CardBody>
            </Card>
          )}
          {extraction && <ExtractionPanel extraction={extraction} onChanged={load} />}
        </div>
      </div>
    </div>
  );
}

function ProvenanceRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-subtle">{label}</span>
      <span className="truncate font-mono text-ink" title={value}>
        {value}
      </span>
    </div>
  );
}
