# ADR-005: Human review as a first-class pipeline stage

## Context

No extraction system is accurate enough that a business can safely act on
100% of its output unsupervised, and this project doesn't pretend otherwise
— see [EVALUATION.md](../EVALUATION.md) for measured numbers and, just as
importantly, what isn't measured yet. The product question stated in
[README.md](../../README.md) is "can a business trust the output enough to stop
retyping it by hand," and the honest answer for *any* extraction accuracy
short of perfect is: for some fraction of documents, not without a human
looking first.

## Decision

Human review is a pipeline stage (`review_routing`) with database-backed
state (`extractions.status`, `needs_review`, `review_reasons`), not an
after-the-fact UI feature bolted onto a "done" pipeline. Every review
decision is recorded (`reviews`), and every field a human actually changes
is recorded separately (`field_corrections`) — see
[DATABASE.md](../DATABASE.md#human-review).

## Alternatives considered

- **No review queue — return confidence scores and let the caller decide.**
  Simpler, and defensible for a pure API product. Rejected for the product
  this is: an SMB customer wants "flag what needs a look," not a raw score
  they have to build their own triage UI around. It also forfeits the
  correction data entirely — there'd be nothing to learn from.
- **Review as a frontend-only concept** (compute a threshold client-side,
  no server-side review state). Rejected because it can't support API/webhook
  consumers uniformly with the UI, can't produce an audit trail, and can't
  feed a future correction-driven feedback loop — all things that require
  the review decision and the correction to be data, not a client-side
  computation that leaves no trace.
- **A single confidence threshold, no per-reason tracking.** Simpler
  schema. Rejected because "needs review" without "why" doesn't tell a
  reviewer where to look — see [AI.md](../AI.md#review-routing) on why a
  document can be routed to review for several independent reasons
  (low overall confidence, one critical field below its own threshold, a
  failed business rule) and why each is recorded individually rather than
  collapsed into one boolean.

## Consequences

- Extra schema (`reviews`, `field_corrections`) and pipeline complexity
  (`review_routing` as its own stage with its own policy) for something a
  pure-API product could skip.
- Correction data is captured with enough context to eventually drive a
  feedback loop (`field_corrections` indexed on `prompt_version, field_path`
  — see [DATABASE.md](../DATABASE.md#human-review)) — built, but **not yet
  consumed by anything automated**. That's future work, stated plainly as
  such rather than implied as already happening.
- The frontend has to render "why does this need review" per field, not
  just a single score — more UI work, but it's what makes the review queue
  actually useful to a reviewer instead of a list of numbers.
