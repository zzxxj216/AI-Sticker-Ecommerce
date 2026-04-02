"""Smoke test for OpenAI-compatible API endpoint.

Usage:
    python scripts/test_claude.py
"""

import os
from dotenv import load_dotenv

load_dotenv(".env", override=True)

os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
response = client.chat.completions.create(
    model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    messages=[{"role": "user", "content": "Hello, world!"}],
)
print(response.choices[0].message.content)
