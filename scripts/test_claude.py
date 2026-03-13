from dotenv import load_dotenv
load_dotenv('.env', override=True)
import anthropic
import os

os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),base_url=os.getenv("ANTHROPIC_BASE_URL"))
response = claude.messages.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Hello, world!"}],
    max_tokens=1000,
    stream=False
)
import sys
text = response.content[0].text
sys.stdout.buffer.write(text.encode(sys.stdout.encoding or "utf-8", errors="replace"))
sys.stdout.buffer.write(b"\n")
