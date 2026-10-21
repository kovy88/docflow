import type { Metadata } from "next";
import { Suspense } from "react";
import { TrialPage } from "@/components/TrialPage";

export const metadata: Metadata = {
  title: "Vyzkoušejte Docflow",
  description:
    "Nahrajte anonymizovanou fakturu, objednávku přepravy nebo nájemní smlouvu a uvidíte vytěžená pole s citací zdroje. Bez registrace, data se mažou do 24 hodin.",
};

export default function Page() {
  // ?ref= is read on the client; Suspense keeps the rest of the page static.
  return (
    <Suspense>
      <TrialPage />
    </Suspense>
  );
}
