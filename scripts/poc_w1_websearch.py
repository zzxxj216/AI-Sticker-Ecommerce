

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

REPORT_PATH = Path("docs/POC_REPORTS/W1_websearch.md")
DEFAULT_QUERY = "graduation 2026 stickers trends"


# ----------------------------------------------------------------------
# Path A — OpenAI Responses API + web_search_preview tool
# ----------------------------------------------------------------------

def try_openai_responses(query: str) -> dict[str, Any]:
    """Call OpenAI Responses API with web_search_preview tool."""
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL") or None
    model = os.getenv("OPENAI_MODEL", "gpt-5.4")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)

    started = time.time()
    try:
        resp = client.responses.create(
            model=model,
            input=f"Search the web for: {query}\n\nReturn 5 fresh sources with title, url, and a one-line summary each.",
            tools=[{"type": "web_search_preview"}],
        )
        elapsed = time.time() - started

        # Extract output_text + citations from the response object.
        out_text = ""
        citations: list[dict[str, str]] = []
        try:
            out_text = resp.output_text or ""
        except AttributeError:
            pass

        # Walk the structured output for any url citations the SDK exposes.
        try:
            for item in resp.output or []:
                content = getattr(item, "content", None) or []
                for c in content:
                    annotations = getattr(c, "annotations", None) or []
                    for a in annotations:
                        url = getattr(a, "url", None)
                        if url:
                            citations.append({
                                "url": url,
                                "title": getattr(a, "title", "") or "",
                            })
        except Exception:
            pass

        # Fallback: harvest URLs out of the raw text if SDK didn't structure them.
        if not citations and out_text:
            for url in re.findall(r"https?://[^\s)\]]+", out_text):
                citations.append({"url": url, "title": ""})

        return {
            "ok": True,
            "elapsed_s": round(elapsed, 2),
            "model": model,
            "base_url": base_url,
            "text": out_text,
            "citations": citations,
            "raw_type": type(resp).__name__,
        }
    except Exception as e:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - started, 2),
            "model": model,
            "base_url": base_url,
            "error": f"{e.__class__.__name__}: {e}",
        }


# ----------------------------------------------------------------------
# Path B — AiHubMix surfing model via Chat Completions
# ----------------------------------------------------------------------

def try_aihubmix_surfing(query: str) -> dict[str, Any]:
    """Call AiHubMix's `gpt-5.4:surfing` (or whatever AIHUBMIX_MODEL is) via Chat."""
    from openai import OpenAI

    api_key = os.getenv("AIHUBMIX_API_KEY", "")
    base_url = os.getenv("AIHUBMIX_BASE_URL", "https://aihubmix.com/v1/chat/completions")
    model = os.getenv("AIHUBMIX_MODEL", "gpt-5.4:surfing")

    # The OpenAI SDK expects a base URL ending at /v1, not /v1/chat/completions.
    if base_url.rstrip("/").endswith("chat/completions"):
        base_url = base_url.rstrip("/").rsplit("/", 2)[0]

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)

    started = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": (
                    f"Search the web for: {query}\n\n"
                    "Return 5 fresh sources. For each, give title, URL, and a one-line summary. "
                    "Format the URLs in plain text so they can be parsed."
                )},
            ],
        )
        elapsed = time.time() - started
        text = resp.choices[0].message.content or ""
        urls = re.findall(r"https?://[^\s)\]]+", text)
        return {
            "ok": True,
            "elapsed_s": round(elapsed, 2),
            "model": model,
            "base_url": base_url,
            "text": text,
            "citations": [{"url": u, "title": ""} for u in urls],
            "usage": getattr(resp, "usage", None).model_dump() if getattr(resp, "usage", None) else {},
        }
    except Exception as e:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - started, 2),
            "model": model,
            "base_url": base_url,
            "error": f"{e.__class__.__name__}: {e}",
        }


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------

def render_report(query: str, a: dict, b: dict) -> str:
    def section(title: str, r: dict) -> str:
        lines = [f"## {title}\n"]
        lines.append(f"- **model**: `{r.get('model')}`")
        lines.append(f"- **base_url**: `{r.get('base_url')}`")
        lines.append(f"- **elapsed**: {r.get('elapsed_s')}s")
        if r.get("ok"):
            lines.append(f"- **status**: ok")
            lines.append(f"- **citations found**: {len(r.get('citations', []))}")
            citations = r.get("citations", [])
            if citations:
                lines.append("- **first 5 URLs**:")
                for c in citations[:5]:
                    lines.append(f"  - {c['url']}")
            txt = (r.get("text") or "").strip()
            if txt:
                preview = txt[:1000]
                lines.append("\n**Text preview (first 1000 chars):**\n")
                lines.append("```")
                lines.append(preview)
                if len(txt) > 1000:
                    lines.append(f"... [truncated, total {len(txt)} chars]")
                lines.append("```")
        else:
            lines.append(f"- **status**: error")
            lines.append(f"- **error**: `{r.get('error')}`")
        return "\n".join(lines)

    verdict = []
    if a.get("ok") and a.get("citations"):
        verdict.append("Path A (OpenAI Responses + web_search_preview) returned citations — viable.")
    elif a.get("ok"):
        verdict.append("Path A succeeded but returned no citations — model may not actually search.")
    else:
        verdict.append("Path A failed — middleman likely doesn't forward Responses API.")
    if b.get("ok") and b.get("citations"):
        verdict.append("Path B (AiHubMix surfing model) returned URLs — viable.")
    elif b.get("ok"):
        verdict.append("Path B succeeded but returned no URLs — model may have answered without searching.")
    else:
        verdict.append("Path B failed.")

    return f"""# POC W1.7 — Web Search via Configured Endpoints

**Run at**: {datetime.now().isoformat(timespec='seconds')}
**Query**: `{query}`

## Verdict

{chr(10).join(f'- {v}' for v in verdict)}

---

{section('Path A — OpenAI Responses API + web_search_preview tool', a)}

---

{section('Path B — AiHubMix surfing model (chat.completions)', b)}

---

## Next steps

If neither path returned real citations, A.1 needs:
1. A native OpenAI key + direct `api.openai.com` endpoint, OR
2. A Tavily / Perplexity API key for a dedicated search provider.

The AIRouter scaffolding (src/services/ai/router.py) already routes
through `web_search()` with a provider list, so adding the winning
provider is a small implementation, not an architecture change.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=DEFAULT_QUERY)
    args = parser.parse_args()

    print(f"=== POC W1.7 web search ===")
    print(f"query: {args.query}\n")

    print("[A] Trying OpenAI Responses API + web_search_preview...")
    a = try_openai_responses(args.query)
    print(f"  -> ok={a.get('ok')} elapsed={a.get('elapsed_s')}s "
          f"citations={len(a.get('citations', []))} error={a.get('error', '')}\n")

    print("[B] Trying AiHubMix surfing model...")
    b = try_aihubmix_surfing(args.query)
    print(f"  -> ok={b.get('ok')} elapsed={b.get('elapsed_s')}s "
          f"citations={len(b.get('citations', []))} error={b.get('error', '')}\n")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(args.query, a, b), encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
