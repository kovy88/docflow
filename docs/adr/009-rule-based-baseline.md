# ADR-009: A rule-based baseline extractor for evaluation comparison

## Context

"Our model gets 90% field accuracy" is not evaluable on its own — 90% is
only meaningful relative to something. Without a baseline, there's no way
to tell whether a model's score reflects genuine document understanding or
just how easy the corpus is (a corpus where every field is trivially
regex-extractable would make *any* reasonable extractor look excellent, model
or not). See [EVALUATION.md](../EVALUATION.md) for what this actually
measures today.

## Decision

A deterministic, keyword/regex/proximity-based extractor
(`backend/src/docflow/eval/` alongside the corpus generator) that runs
against the exact same corpus and is scored with the exact same metrics as
any LLM-backed run. It's also wrapped behind the `LLMProvider` interface as
the `FixtureProvider` (see [ADR-004](004-llm-provider-abstraction.md)) so
the *pipeline* has a zero-cost, zero-credential extractor to run against for
tests and demos — the evaluation baseline and the demo fixture are the same
underlying logic, used for two different purposes.

## Alternatives considered

- **No baseline — report the model's raw accuracy number alone.** The
  simplest option, and the most likely to mislead: a bare accuracy number
  invites the reader to compare it against an intuition of "how hard should
  this be," which is exactly the comparison this project's stated principle
  (never invent or imply a number that wasn't measured) rules out relying
  on. A measured floor is what makes the number interpretable.
- **A second LLM as the "baseline"** (e.g., a smaller/cheaper model). Would
  answer a different question — "is the expensive model better than the
  cheap model" — not "is an LLM approach earning its cost and complexity
  over a simpler one." A rule-based baseline answers the harder, more
  useful question: does this need a model at all, for the fields where it
  doesn't.
- **Skip a baseline, report only human-in-the-loop review rate.** Review
  rate matters (it's in the same report), but it conflates "the model was
  wrong" with "the model was right but the confidence system was
  conservative" — a baseline field-accuracy comparison isolates extraction
  quality specifically.

## Consequences

- The baseline is deliberately weak on anything requiring real language
  understanding — nested objects and table/line-item extraction score 0%
  ([EVALUATION.md](../EVALUATION.md#where-the-baseline-fails-completely))
  by design, not as a bug to fix. A baseline that could do everything a
  model can wouldn't be a useful floor.
- Because the fixture provider *is* the baseline, running the pipeline
  end-to-end with the baseline "extracting" through the LLM interface risks
  the model appearing to agree with itself on every field (baseline
  cross-check confidence signal). This is guarded against explicitly — see
  [AI.md](../AI.md#confidence-scoring) — as a direct, known consequence of
  reusing the same logic in two roles.
- Every evaluation report has to run and show both rows (baseline and
  whatever's under test) rather than the tested extractor alone, which is
  slightly more report to read but is what makes the accuracy number
  interpretable instead of a bare percentage.
