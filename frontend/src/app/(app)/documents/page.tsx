"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { documents, type DocumentSummary, type Page } from "@/lib/api";
import { formatBytes, formatRelativeTime, formatDuration, cn } from "@/lib/utils";
import { Card, EmptyState, Input, Skeleton, StatusBadge } from "@/components/ui";
import { UploadDropzone } from "@/components/upload-dropzone";
import { Icons } from "@/components/app-shell";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "needs_review", label: "Needs review" },
  { value: "processing", label: "Processing" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
] as const;

export default function DocumentsPage() {
  const [page, setPage] = useState<Page<DocumentSummary> | null>(null);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await documents.list({ status: status || undefined, search: search || undefined, limit: 50 });
      setPage(result);
    } finally {
      setLoading(false);
    }
  }, [status, search]);

  useEffect(() => {
    void load();
  }, [load]);

  // Light polling while anything is in flight, so a status change (queued ->
  // processing -> completed) shows up without a manual refresh. Stops itself
  // once nothing is moving, so an idle tab does not poll forever.
  useEffect(() => {
    const inFlight = page?.items.some((d) => d.status === "queued" || d.status === "processing");
    if (!inFlight) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [page, load]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Documents</h1>
        <p className="mt-1 text-sm text-subtle">Upload, track and review document processing.</p>
      </div>

      <UploadDropzone />

      <div className="flex items-center gap-3">
        <div className="flex gap-1 rounded-lg bg-muted p-1">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.value}
              onClick={() => setStatus(filter.value)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                status === filter.value ? "bg-surface text-ink shadow-sm" : "text-subtle hover:text-ink",
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <Input
          placeholder="Search filename…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
      </div>

      <Card>
        {loading && !page && (
          <div className="divide-y divide-line">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-4 w-20" />
                <Skeleton className="ml-auto h-4 w-24" />
              </div>
            ))}
          </div>
        )}

        {page && page.items.length === 0 && (
          <EmptyState
            icon={<Icons.File className="h-8 w-8" />}
            title="No documents yet"
            description="Upload your first document above to see it processed here."
          />
        )}

        {page && page.items.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs font-medium text-subtle">
                <th className="px-5 py-3 font-medium">Filename</th>
                <th className="px-5 py-3 font-medium">Type</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Uploaded</th>
                <th className="px-5 py-3 font-medium">Processing time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {page.items.map((doc) => (
                <tr key={doc.id} className="transition-colors hover:bg-muted/50">
                  <td className="px-5 py-3.5">
                    <Link href={`/documents/${doc.id}`} className="font-medium text-ink hover:text-brand">
                      {doc.filename}
                    </Link>
                    <p className="text-xs text-subtle">{formatBytes(doc.size_bytes)}</p>
                  </td>
                  <td className="px-5 py-3.5 text-subtle">{doc.document_type_key ?? "—"}</td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={doc.status} />
                  </td>
                  <td className="px-5 py-3.5 text-subtle">{formatRelativeTime(doc.created_at)}</td>
                  <td className="px-5 py-3.5 text-subtle">{formatDuration(doc.processing_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {page && page.total > page.items.length && (
        <p className="text-center text-sm text-subtle">
          Showing {page.items.length} of {page.total}
        </p>
      )}
    </div>
  );
}
