"""Quick check of the Hugging Face router: python hf_test.py "your prompt" """
import os
import sys

from openai import OpenAI

from doctestlx.config import load_dotenv

load_dotenv()
prompt = " ".join(sys.argv[1:]) or 'Reply with {"ok": true} as JSON.'
client = OpenAI(base_url="https://router.huggingface.co/v1", api_key=os.environ["HF_TOKEN"])
resp = client.chat.completions.create(
    model="Qwen/Qwen2.5-7B-Instruct",
    messages=[{"role": "user", "content": prompt}],
    temperature=0,
)
print(resp.choices[0].message.content)
