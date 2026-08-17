# ADR-004: A provider abstraction over the LLM, not a direct SDK dependency

## Context

Model landscape and pricing shift fast enough that a document-processing
product hardcoded to one vendor's SDK is a real business risk, not just an
engineering purity concern — a customer's contract, a pricing change, or a
capability gap can force a provider switch on a timeline the business
doesn't choose. Separately, tests, CI, and demos need to run without a real
API key and without real API cost or latency, and without becoming flaky
because a third-party service is unavailable.

## Decision

One `LLMProvider` interface (`complete_structured`, `health_check`,
`aclose`); `AnthropicProvider`, `OpenAIProvider`, `GeminiProvider`, and a
`FixtureProvider` implement it. Everything above the interface — the
pipeline, classification, confidence scoring — depends only on the
interface. See [AI.md](../AI.md#provider-abstraction).

## Alternatives considered

- **Call the Anthropic SDK directly from the pipeline.** Less code today.
  Rejected because it makes "run the tests" and "run the demo" both require
  a paid API key and network access, and it means a provider or pricing
  change is a multi-file refactor instead of a new class implementing an
  existing interface.
- **A third-party abstraction layer** (e.g. LiteLLM or similar
  multi-provider proxies). Would solve the same problem with less code
  written here, at the cost of a dependency on that library's own
  abstraction choices and its pace of tracking each vendor's structured-
  output API (which has genuine per-vendor differences — see
  [AI.md](../AI.md#structured-output-not-free-text) on JSON Schema
  normalization). For two providers plus a fixture at the time of this
  decision, a ~100-line interface this codebase controls fully was judged
  simpler than adopting and tracking a third-party abstraction's own
  versioning.

## Consequences

- Adding another real provider, or swapping which one is default, touches
  one new file and a config value — not pipeline code. Borne out in
  practice: `GeminiProvider` was added after this decision was made, and it
  cost exactly that — one file (`llm/google_provider.py`) and a config
  value, zero changes to the pipeline, classification, or confidence-scoring
  code that depends on the interface.
- The fixture provider is a first-class implementation of the same
  interface, not a mock framework's patch target — which is what makes
  `docker compose up && docflow-seed` work with zero credentials and still
  exercise the real classification/validation/confidence-scoring code paths
  ([LOCAL_DEVELOPMENT.md](../LOCAL_DEVELOPMENT.md#running-without-a-real-llm-key)).
- Structured-output schemas have to be normalized to a common subset across
  providers (`llm/schema.py`) — a small, ongoing tax paid once at the
  interface boundary instead of scattered wherever a call happened to be
  made.
- The interface has to be conservative about what it exposes — it can only
  offer capabilities present across every provider it might ever wrap
  (structured output; not, for instance, a feature specific to one vendor)
  unless that capability is made optional. Nothing in this codebase has
  needed a provider-specific escape hatch yet.
