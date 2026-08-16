# ADR-008: Three separate validation layers, not one

## Context

"The extraction is valid" means several genuinely different things: *is
this the right shape* (a date field actually contains a date), *is this
internally consistent* (line items sum to the subtotal), and *does this
satisfy this document type's business rules* (an invoice has at least one
line item; a purchase order references a valid vendor). Collapsing these
into one validation pass either means a single function that knows about
JSON typing, arithmetic, locale-aware parsing, and per-document-type
policy all at once, or means giving up on some of those checks.

## Decision

Three layers, run in order, each with a distinct failure mode
(see [ARCHITECTURE.md](../ARCHITECTURE.md#the-pipeline)):

1. **Syntax** — the Pydantic schema the LLM's structured output is already
   constrained to. A malformed response fails here, cheaply, before any
   other stage runs.
2. **Semantic** — cross-field arithmetic, date sanity, locale-aware number
   parsing, and country-specific checksums (IBAN mod-97, Czech IČO mod-11,
   ČNB account checksum) — logic that's true regardless of document type.
3. **Business rules** — a per-document-type rule registry
   (`backend/src/docflow/validation/`), with each rule isolated so one
   failing rule doesn't take the rest down with it.

## Alternatives considered

- **One monolithic validation function per document type.** Would work, but
  means locale-aware date/number parsing and checksum logic gets
  copy-pasted or awkwardly shared across every document type's validator,
  and a bug in one document type's validation function can't be reasoned
  about independently of the others.
- **Validation entirely inside the Pydantic schema** (custom validators on
  every field). Pydantic validators run per-field during parsing, which is
  the wrong place for cross-field checks (line items vs. subtotal) and
  business-policy checks that depend on the document type, not just the
  field's own type — and a validator raising there fails the whole parse
  rather than producing a validation *issue* the confidence-scoring stage
  can weigh.
- **No business-rule layer — semantic checks only.** Simpler, but loses the
  ability to express "this document type requires X" as data
  (`document_types` rows referencing rule ids —
  [DATABASE.md](../DATABASE.md#document-configuration)) rather than as code
  specific to one type, which is what makes adding a fifth document type
  later a data change, not a new validator module.

## Consequences

- A validation failure at layer 1 (syntax) means layers 2 and 3 never run
  for that field — cheaper, and correct, since semantic checks on a value
  that isn't even the right type would be meaningless.
- Layer 3's rule registry isolates failures per rule
  ([ARCHITECTURE.md](../ARCHITECTURE.md#the-pipeline)) — one broken rule
  produces one validation issue, not an exception that aborts validation for
  every other rule on the document.
- Confidence scoring reads validation *issues*, not a pass/fail boolean —
  a field that failed validation scores near zero on that signal, a field
  with only a warning scores partway down, which is only possible because
  validation produces structured issues with severity rather than raising on
  first failure. See [AI.md](../AI.md#confidence-scoring).
- Locale-aware parsing (day/month ambiguity, decimal-comma numbers,
  country-specific checksums) lives in one place (layer 2) shared by every
  document type, instead of being reimplemented per type.
