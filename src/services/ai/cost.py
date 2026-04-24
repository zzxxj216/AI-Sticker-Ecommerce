"""Rough cost estimates for AI provider calls.

These are USD per 1k tokens (input, output). Numbers are approximate and
intended for budgeting / observability only — not billing. Update as
provider pricing or aihubmix rates change.
"""

from __future__ import annotations

# (input_per_1k_usd, output_per_1k_usd)
TEXT_COST_PER_1K: dict[str, tuple[float, float]] = {
    # OpenAI (direct or via aihubmix)
    "gpt-5.4":               (5.00, 15.00),
    "gpt-5.4-pro":           (15.00, 60.00),
    "gpt-4o":                (2.50, 10.00),
    "gpt-4o-mini":           (0.15, 0.60),
    "gpt-4o-search-preview": (2.50, 10.00),
    "gpt-4.1":               (2.00, 8.00),
    "gpt-4.1-mini":          (0.40, 1.60),
    # Anthropic
    "claude-opus-4":         (15.00, 75.00),
    "claude-sonnet-4":       (3.00, 15.00),
    "claude-haiku-4":        (0.25, 1.25),
    # Gemini
    "gemini-2.0-flash":      (0.075, 0.30),
    "gemini-2.0-pro":        (1.25, 5.00),
    "gemini-2.5-pro":        (1.25, 5.00),
}

# Per-image flat estimate (USD); refined later if real pricing varies by size.
IMAGE_COST_PER_CALL: dict[str, float] = {
    "gpt-image-1": 0.04,
    "gpt-image-2": 0.06,
    "dall-e-3":    0.04,
}

# Per-search-call estimate (USD).
SEARCH_COST_PER_CALL: dict[str, float] = {
    "tavily":      0.005,
    "perplexity":  0.005,
    "openai_web_search": 0.025,  # Responses API web_search_preview tool surcharge
}


def estimate_text_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return cost in USD for a text-completion call. Returns 0.0 if model unknown."""
    rates = TEXT_COST_PER_1K.get(model)
    if not rates:
        # Unknown model — try a fuzzy prefix match before giving up.
        for known, r in TEXT_COST_PER_1K.items():
            if model.startswith(known):
                rates = r
                break
    if not rates:
        return 0.0
    in_cost = (input_tokens / 1000.0) * rates[0]
    out_cost = (output_tokens / 1000.0) * rates[1]
    return round(in_cost + out_cost, 6)


def estimate_image_cost(model: str, n: int = 1) -> float:
    return IMAGE_COST_PER_CALL.get(model, 0.05) * n


def estimate_search_cost(provider: str) -> float:
    return SEARCH_COST_PER_CALL.get(provider, 0.01)
