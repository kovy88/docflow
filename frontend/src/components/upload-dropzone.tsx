"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { documents, ApiError } from "@/lib/api";
import { Button, Spinner } from "@/components/ui";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/toast-context";

const ACCEPTED = ".pdf,.png,.jpg,.jpeg,.tiff,.webp,.txt,.docx";

export function UploadDropzone() {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const notify = useToast();

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      try {
        const result = await documents.upload(file);
        if (result.duplicate) {
          notify("brand", "This document was already uploaded — showing the existing record.");
        } else {
          notify("ok", `${file.name} uploaded — processing started.`);
        }
        router.push(`/documents/${result.document_id}`);
      } catch (err) {
        notify("danger", err instanceof ApiError ? err.message : "Upload failed.");
      } finally {
        setUploading(false);
      }
    },
    [router, notify],
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file) void upload(file);
      }}
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
        dragging ? "border-brand bg-brand/5" : "border-line bg-muted/50",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
          e.target.value = "";
        }}
      />
      {uploading ? (
        <>
          <Spinner className="h-6 w-6 text-brand" />
          <p className="text-sm text-subtle">Uploading…</p>
        </>
      ) : (
        <>
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 text-brand">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
              <path d="M17 8l-5-5-5 5" />
              <path d="M12 3v12" />
            </svg>
          </div>
          <div>
            <p className="text-sm font-medium text-ink">
              Drag a document here, or{" "}
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                className="text-brand hover:underline"
              >
                browse
              </button>
            </p>
            <p className="mt-1 text-xs text-subtle">PDF, image, DOCX or plain text — up to 20 MB</p>
          </div>
          <Button size="sm" variant="outline" onClick={() => inputRef.current?.click()}>
            Choose file
          </Button>
        </>
      )}
    </div>
  );
}
