from dotenv import load_dotenv
from openai import OpenAI
load_dotenv('.env', override=True)
import anthropic
import os

os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
client = OpenAI(api_key='sk_LAwReeB22oqhNb7K0MJFVP_33WHbJDvJeBpWYH_o9zQ',base_url='https://api.jiekou.ai/openai/')
response = client.chat.completions.create(
    model="gpt-5.4",
    messages=[{"role": "user", "content": "Hello, world!"}],
    reasoning_effort="medium"
)
print(response.choices[0].message.content)