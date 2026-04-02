"""Quick debug: print what Config actually loads."""
import sys, os

from scripts.script_utils import PROJECT_ROOT

print("=== Raw os.getenv ===")
print(f"  ANTHROPIC_API_KEY = {os.getenv('ANTHROPIC_API_KEY', '(not set)')[:15]}...")
print(f"  ANTHROPIC_BASE_URL = {os.getenv('ANTHROPIC_BASE_URL', '(not set)')}")
print(f"  CLAUDE_MODEL = {os.getenv('CLAUDE_MODEL', '(not set)')}")

from src.core.config import config

print("\n=== Config singleton ===")
print(f"  claude_api_key = {config.claude_api_key[:15]}...")
print(f"  claude_base_url = {config.claude_base_url}")
print(f"  claude_model = {config.claude_model}")

import anthropic
client = anthropic.Anthropic(
    api_key=config.claude_api_key,
    base_url=config.claude_base_url,
)
print(f"\n=== Anthropic client ===")
print(f"  base_url = {client.base_url}")
print(f"  api_key = {client.api_key[:15]}...")

print("\n=== Quick API test ===")
try:
    r = client.messages.create(
        model=config.claude_model,
        messages=[{"role": "user", "content": "Say hi in 3 words"}],
        max_tokens=20,
    )
    print(f"  OK: {r.content[0].text}")
except Exception as e:
    print(f"  ERROR: {e}")
