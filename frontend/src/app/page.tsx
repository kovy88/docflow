import Link from "next/link";
import { PlainHeader } from "@/components/app-shell";
import { Button, Card, CardBody } from "@/components/ui";

const WORKFLOW = [
  "Upload a PDF, scan or DOCX",
  "Text extraction with automatic OCR fallback",
  "Document classified against your configured types",
  "LLM extraction into a validated structured schema",
  "Three-layer validation: syntax, arithmetic, business rules",
  "Per-field confidence scoring",
  "Low-confidence documents routed to a human reviewer",
  "Approved data exported or pushed via webhook",
];

const PRICING = [
  { name: "Free", price: "$0", quota: "50 docs / month", features: ["1 document type", "Email support"] },
  { name: "Starter", price: "$49", quota: "500 docs / month", features: ["All document types", "Webhooks", "CSV/JSON export"] },
  { name: "Business", price: "$199", quota: "3,000 docs / month", features: ["Custom document types", "API access", "Priority support"] },
  { name: "Enterprise", price: "Custom", quota: "Unlimited", features: ["Custom processing", "SSO", "Dedicated support"] },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen">
      <PlainHeader />

      <section className="mx-auto max-w-4xl px-6 py-20 text-center">
        <h1 className="text-4xl font-semibold tracking-tight text-ink sm:text-5xl">
          Turn unstructured documents into
          <br />
          <span className="text-brand">validated structured data</span>
        </h1>
        <p className="mx-auto mt-5 max-w-xl text-lg text-subtle">
          Invoices, contracts, purchase orders and receipts — extracted, validated and
          confidence-scored automatically, with a human in the loop for anything uncertain.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Link href="/register">
            <Button size="lg">Start free</Button>
          </Link>
          <Link href="/login">
            <Button size="lg" variant="outline">
              Sign in
            </Button>
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-4xl px-6 pb-20">
        <Card>
          <CardBody className="p-8">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-subtle">
              How a document moves through the system
            </h2>
            <ol className="mt-4 space-y-3">
              {WORKFLOW.map((step, i) => (
                <li key={step} className="flex items-center gap-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/10 text-xs font-semibold text-brand">
                    {i + 1}
                  </span>
                  <span className="text-sm text-ink">{step}</span>
                </li>
              ))}
            </ol>
          </CardBody>
        </Card>
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-24">
        <h2 className="text-center text-2xl font-semibold text-ink">Pricing</h2>
        <p className="mt-2 text-center text-sm text-subtle">
          Concept pricing for a demo product — no billing is processed.
        </p>
        <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PRICING.map((plan) => (
            <Card key={plan.name}>
              <CardBody>
                <p className="text-sm font-semibold text-ink">{plan.name}</p>
                <p className="mt-2 text-2xl font-semibold text-ink">
                  {plan.price}
                  {plan.price !== "Custom" && <span className="text-sm font-normal text-subtle">/mo</span>}
                </p>
                <p className="mt-1 text-xs text-subtle">{plan.quota}</p>
                <ul className="mt-4 space-y-1.5">
                  {plan.features.map((f) => (
                    <li key={f} className="text-xs text-subtle">
                      • {f}
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          ))}
        </div>
      </section>

      <footer className="border-t border-line py-8 text-center text-xs text-subtle">
        Docflow is a demonstration product. Organizations shown in the app are example/demo data.
      </footer>
    </div>
  );
}
