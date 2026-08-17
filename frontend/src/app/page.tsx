"use client";

import Link from "next/link";
import { PlainHeader, Icons } from "@/components/app-shell";
import { Button, Card, CardBody, Badge } from "@/components/ui";

const FEATURES = [
  {
    icon: Icons.Layers,
    title: "Three-layer validation",
    description:
      "Syntax, arithmetic and business-rule checks run on every extraction, independent of the model — catches what the LLM gets wrong instead of trusting it blindly.",
  },
  {
    icon: Icons.Users,
    title: "Human in the loop",
    description:
      "Per-field confidence scoring routes anything uncertain to a reviewer automatically, with the source text and reasoning alongside it.",
  },
  {
    icon: Icons.Swap,
    title: "Swappable LLM provider",
    description:
      "Anthropic, OpenAI or Google behind one interface. Change providers or models from config, not a rewrite, and compare them on real accuracy numbers.",
  },
];

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
  {
    name: "Starter",
    price: "$49",
    quota: "500 docs / month",
    features: ["All document types", "Webhooks", "CSV/JSON export"],
    recommended: true,
  },
  { name: "Business", price: "$199", quota: "3,000 docs / month", features: ["Custom document types", "API access", "Priority support"] },
  { name: "Enterprise", price: "Custom", quota: "Unlimited", features: ["Custom processing", "SSO", "Dedicated support"] },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen">
      <PlainHeader />

      <section className="landing-glow relative overflow-hidden">
        <div className="landing-grid absolute inset-0" />
        <div className="relative mx-auto max-w-4xl px-6 py-24 text-center">
          <div className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-subtle shadow-sm">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" />
            Live demo — real pipeline, real numbers
          </div>
          <h1 className="mx-auto mt-6 max-w-3xl text-4xl font-semibold tracking-tight text-ink sm:text-5xl">
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
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-20">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {FEATURES.map((feature) => (
            <Card key={feature.title}>
              <CardBody className="p-6">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand/10 text-brand">
                  <feature.icon className="h-4.5 w-4.5" />
                </div>
                <h3 className="mt-4 text-sm font-semibold text-ink">{feature.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-subtle">{feature.description}</p>
              </CardBody>
            </Card>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-4xl px-6 pb-20">
        <Card>
          <CardBody className="p-8">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-subtle">
              How a document moves through the system
            </h2>
            <ol className="mt-5 space-y-3">
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
        <h2 className="text-center text-2xl font-semibold tracking-tight text-ink">Pricing</h2>
        <p className="mt-2 text-center text-sm text-subtle">
          Concept pricing for a demo product — no billing is processed.
        </p>
        <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PRICING.map((plan) => (
            <Card
              key={plan.name}
              className={plan.recommended ? "border-brand/50 ring-1 ring-brand/20" : undefined}
            >
              <CardBody>
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-ink">{plan.name}</p>
                  {plan.recommended && <Badge tone="brand">Popular</Badge>}
                </div>
                <p className="mt-2 text-2xl font-semibold tracking-tight text-ink">
                  {plan.price}
                  {plan.price !== "Custom" && <span className="text-sm font-normal text-subtle">/mo</span>}
                </p>
                <p className="mt-1 text-xs text-subtle">{plan.quota}</p>
                <ul className="mt-4 space-y-1.5">
                  {plan.features.map((f) => (
                    <li key={f} className="flex items-center gap-1.5 text-xs text-subtle">
                      <Icons.Check className="h-3 w-3 shrink-0 text-ok" />
                      {f}
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          ))}
        </div>
      </section>

      <footer className="border-t border-line py-10">
        <div className="mx-auto flex max-w-5xl flex-col items-center gap-3 px-6 text-center">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink">
            <Icons.Logo className="h-4 w-4 text-brand" />
            Docflow
          </div>
          <p className="max-w-md text-xs text-subtle">
            Docflow is a demonstration product. Organizations shown in the app are example/demo data.
          </p>
        </div>
      </footer>
    </div>
  );
}
