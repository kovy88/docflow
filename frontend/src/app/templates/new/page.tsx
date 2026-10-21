"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

// The draft lives in sessionStorage, so this page renders only in the browser.
const NewTemplate = dynamic(
  () => import("@/components/NewTemplate").then((m) => m.NewTemplate),
  { ssr: false, loading: () => <p className="p-6 text-muted">Načítám…</p> },
);

export default function NewTemplatePage() {
  return <Suspense fallback={<p className="p-6 text-muted">Načítám…</p>}><TemplateDraft /></Suspense>;
}

function TemplateDraft() {
  const search = useSearchParams();
  return <NewTemplate key={search.get("draft") ?? "empty"} />;
}
