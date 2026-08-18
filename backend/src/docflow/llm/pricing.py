"""Token pricing.

Prices are USD per **million** tokens and are the published list rates as of the
date below. They are used for cost *estimation* — the authoritative number is the
provider's own invoice, and the dashboard says so.

Keeping this as an explicit table rather than a live API call is deliberate: cost
attribution must be computable at write time, inside the same transaction that
records the extraction, without a network dependency that can fail and leave a row
with no cost attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Update this when the table below changes. Surfaced in the usage dashboard so a
# stale table is visible rather than silently wrong.
PRICING_AS_OF = "2026-06-24"

MILLION = Decimal("1000000")


@dataclass(frozen=True, slots=True)
class ModelPricing:
    provider: str
    model: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    # Cached input is billed at a fraction of the input rate. Prompt caching is not
    # enabled in the current extraction path (each document is a distinct prefix and
    # the shared prefix falls below the caching minimum), but the field exists so
    # that enabling it does not require a schema change.
    cached_input_per_mtok: Decimal | None = None
    context_window: int = 200_000
    notes: str = ""


def _d(value: str) -> Decimal:
    return Decimal(value)


PRICING: dict[str, ModelPricing] = {
    # --- Anthropic -----------------------------------------------------------
    "claude-opus-5": ModelPricing(
        "anthropic",
        "claude-opus-5",
        _d("5.00"),
        _d("25.00"),
        cached_input_per_mtok=_d("0.50"),
        context_window=1_000_000,
    ),
    "claude-opus-4-8": ModelPricing(
        "anthropic",
        "claude-opus-4-8",
        _d("5.00"),
        _d("25.00"),
        cached_input_per_mtok=_d("0.50"),
        context_window=1_000_000,
    ),
    "claude-sonnet-5": ModelPricing(
        "anthropic",
        "claude-sonnet-5",
        _d("3.00"),
        _d("15.00"),
        cached_input_per_mtok=_d("0.30"),
        context_window=1_000_000,
        notes="Introductory pricing of $2.00/$10.00 applies through 2026-08-31.",
    ),
    "claude-sonnet-4-6": ModelPricing(
        "anthropic",
        "claude-sonnet-4-6",
        _d("3.00"),
        _d("15.00"),
        cached_input_per_mtok=_d("0.30"),
        context_window=1_000_000,
    ),
    "claude-haiku-4-5": ModelPricing(
        "anthropic",
        "claude-haiku-4-5",
        _d("1.00"),
        _d("5.00"),
        cached_input_per_mtok=_d("0.10"),
        context_window=200_000,
        notes="Cheapest supported option; see docs/AI.md for the accuracy trade-off.",
    ),
    # --- Google ----------------------------------------------------------------
    # Source: https://ai.google.dev/gemini-api/docs/pricing (fetched 2026-08-16).
    "gemini-3.6-flash": ModelPricing(
        "google",
        "gemini-3.6-flash",
        _d("0.75"),
        _d("3.75"),
        context_window=1_000_000,
        notes="Standard tier through 2026-12-31; rises to $1.50/$7.50 per MTok "
        "on 2027-01-01 per Google's published schedule.",
    ),
    # --- OpenAI --------------------------------------------------------------
    # Listed so the provider abstraction is exercised by a second real vendor.
    # Verify against the vendor's current price list before relying on these for
    # billing; they are estimates in exactly the same sense as the rows above.
    "gpt-4.1": ModelPricing("openai", "gpt-4.1", _d("2.00"), _d("8.00"), context_window=1_000_000),
    "gpt-4.1-mini": ModelPricing(
        "openai", "gpt-4.1-mini", _d("0.40"), _d("1.60"), context_window=1_000_000
    ),
    "gpt-5.6-luna": ModelPricing(
        "openai",
        "gpt-5.6-luna",
        _d("0.10"),
        _d("0.60"),
        context_window=1_050_000,
        notes="Fastest/cheapest tier of the GPT-5.6 family (flagship is Sol, "
        "mid-tier is Terra). Sourced from third-party model listings (OpenRouter, "
        "AWS Bedrock, Cloudflare) — openai.com is not reachable from this "
        "environment to confirm directly.",
    ),
    # --- Fixture -------------------------------------------------------------
    # Deterministic local provider. Free by construction — it makes no API call.
    "fixture-heuristic": ModelPricing(
        "fixture",
        "fixture-heuristic",
        _d("0"),
        _d("0"),
        context_window=1_000_000,
        notes="Deterministic local extractor. Not an LLM. Produces no cost.",
    ),
    "fixture-replay": ModelPricing(
        "fixture",
        "fixture-replay",
        _d("0"),
        _d("0"),
        context_window=1_000_000,
        notes="Replays recorded provider responses. Produces no cost.",
    ),
}


def get_pricing(model: str) -> ModelPricing | None:
    return PRICING.get(model)


def estimate_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> Decimal:
    """Cost in USD, rounded to 6 decimal places.

    An unknown model returns 0 rather than raising: failing a document because we
    cannot price it would be the wrong trade — the work is already done, and the
    unpriced usage row is visible in the dashboard.
    """
    pricing = PRICING.get(model)
    if pricing is None:
        return Decimal("0")

    billable_input = max(0, input_tokens - cached_input_tokens)
    cost = (
        Decimal(billable_input) * pricing.input_per_mtok
        + Decimal(output_tokens) * pricing.output_per_mtok
    ) / MILLION

    if cached_input_tokens and pricing.cached_input_per_mtok is not None:
        cost += (Decimal(cached_input_tokens) * pricing.cached_input_per_mtok) / MILLION

    return cost.quantize(Decimal("0.000001"))


def estimate_tokens(text: str) -> int:
    """Rough token estimate for pre-flight budgeting only.

    Deliberately not a tokenizer. It is used to decide whether a document must be
    truncated before the call and to reject work that would blow the per-document
    cost ceiling; both only need an order-of-magnitude answer. Actual billing uses
    the token counts the provider reports.

    ~3.6 characters per token is a reasonable average for the mixed
    English/Czech business text this system sees. Under-estimating is the dangerous
    direction, so the divisor is on the low side.
    """
    return max(1, len(text) // 3)
