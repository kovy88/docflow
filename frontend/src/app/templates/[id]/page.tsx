import { Suspense } from "react";
import { TemplateDetail } from "@/components/TemplateDetail";

export default function TemplatePage({ params }: PageProps<"/templates/[id]">) {
  return (
    <Suspense fallback={<p className="p-6 text-muted">Načítám…</p>}>
      <Detail params={params} />
    </Suspense>
  );
}

async function Detail({
  params,
}: {
  params: PageProps<"/templates/[id]">["params"];
}) {
  const { id } = await params;
  return <TemplateDetail id={decodeURIComponent(id)} />;
}
