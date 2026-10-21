import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Workspace } from "@/components/Workspace";

export default function DocumentPage({ params }: PageProps<"/documents/[id]">) {
  return (
    <Suspense fallback={<p className="p-6 text-muted">Načítám…</p>}>
      <DocumentWorkspace params={params} />
    </Suspense>
  );
}

async function DocumentWorkspace({
  params,
}: {
  params: PageProps<"/documents/[id]">["params"];
}) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) notFound();
  return <Workspace key={id} documentId={id} />;
}
