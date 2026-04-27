"""Rough cost estimates for AI provider calls.

USD per 1k tokens (input, output). For budgeting / observability, NOT
billing. Calibrated against AiHubMix's published rate card, which is
roughly proportional to OpenAI's listed prices but charged in CNY ÷ 7.

KNOWN INACCURACIES:
- "gpt-5.4" / "gpt-5.4-pro" are AiHubMix-branded model names whose
  underlying provider isn't documented; I've placed them at the upper
  end of "fast multimodal class" pricing. Replace with invoice-derived
  numbers once the operator can share an AiHubMix monthly statement.
- Per-image and per-search numbers are flat estimates that ignore
  size / quality multipliers.
"""

from __future__ import annotations

# (input_per_1k_usd, output_per_1k_usd)
TEXT_COST_PER_1K: dict[str, tuple[float, float]] = {
    # OpenAI (direct or via aihubmix). Numbers are AiHubMix-flavoured
    # estimates — aihubmix.com publishes prices in 元 (1k tokens) and the
    # USD equivalents below assume ~7 CNY/USD.
    # Surfing variant adds a per-call websearch surcharge but token cost
    # tracks the base model.
    "gpt-5.4":                 (0.30, 1.20),
    "gpt-5.4:surfing":         (0.30, 1.20),
    "gpt-5.4-pro":             (1.50, 6.00),
    "gpt-4o":                  (2.50, 10.00),
    "gpt-4o-mini":             (0.15, 0.60),
    "gpt-4o-search-preview":   (2.50, 10.00),
    "gpt-4.1":                 (2.00, 8.00),
    "gpt-4.1-mini":            (0.40, 1.60),
    # Anthropic (proxied)
    "claude-opus-4":           (15.00, 75.00),
    "claude-sonnet-4":         (3.00, 15.00),
    "claude-haiku-4":          (0.25, 1.25),
    # Gemini (proxied)
    "gemini-2.0-flash":        (0.075, 0.30),
    "gemini-2.0-pro":          (1.25, 5.00),
    "gemini-2.5-pro":          (1.25, 5.00),
}

# Per-image flat estimate (USD); refined later if real pricing varies by size.
IMAGE_COST_PER_CALL: dict[str, float] = {
    "gpt-image-1": 0.04,
    "gpt-image-2": 0.06,
    "dall-e-3":    0.04,
}

# Per-search-call surcharge (USD). For surfing models this is ON TOP OF
# the token cost charged by TEXT_COST_PER_1K.
SEARCH_COST_PER_CALL: dict[str, float] = {
    "tavily":             0.005,
    "perplexity":         0.005,
    "openai_web_search":  0.025,  # Responses API web_search_preview tool surcharge
    "aihubmix_surfing":   0.010,  # AiHubMix websearch surcharge — best-effort
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
