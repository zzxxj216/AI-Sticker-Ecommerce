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

# (input_per_1k_usd, output_per_1k_usd) — derived by dividing each
# vendor's published per-1M price by 1000. Earlier versions of this
# file mistakenly stored per-1M values, which 1000×-inflated the cost
# column in ai_call_logs (e.g. a $2.82 entry should have been $0.0028).
TEXT_COST_PER_1K: dict[str, tuple[float, float]] = {
    # OpenAI (direct or via JieKou / AiHubMix proxies)
    "gpt-5.4":                 (0.0003,  0.0012),
    "gpt-5.4:surfing":         (0.0003,  0.0012),
    "gpt-5.4-pro":             (0.0015,  0.0060),
    "gpt-4o":                  (0.0025,  0.0100),
    "gpt-4o-mini":             (0.00015, 0.00060),
    "gpt-4o-search-preview":   (0.0025,  0.0100),
    "gpt-4.1":                 (0.0020,  0.0080),
    "gpt-4.1-mini":            (0.00040, 0.00160),
    # Anthropic (proxied)
    "claude-opus-4":           (0.0150,  0.0750),
    "claude-sonnet-4":         (0.0030,  0.0150),
    "claude-haiku-4":          (0.00025, 0.00125),
    # Gemini (proxied)
    "gemini-2.0-flash":        (0.000075, 0.00030),
    "gemini-2.0-pro":          (0.00125, 0.00500),
    "gemini-2.5-pro":          (0.00125, 0.00500),
}

# Per-image flat estimate (USD). gpt-image-2 cost varies by quality
# (low/medium/high); these are mid-quality 1024x1024 estimates.
IMAGE_COST_PER_CALL: dict[str, float] = {
    "gpt-image-1":   0.04,
    "gpt-image-1.5": 0.04,   # JieKou's "GPT Image 2" doc names this gpt-image-1.5 in practice
    "gpt-image-2":   0.05,
    "dall-e-3":      0.04,
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
