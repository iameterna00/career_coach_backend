import os
import httpx

# Load API key from .env
OPENAI_API_KEY = os.environ.get("OPENAI_API")
DEESEEK_API_KEY = os.environ.get("DEEPSEEK_API")
DEESEEK_BASE_URL = "https://api.deepseek.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set in .env")

if not DEESEEK_API_KEY:
    raise RuntimeError("DEESEEK_API not set in .env")

def generate_deepseek_reply(messages: list) -> str:
    headers = {"Authorization": f"Bearer {DEESEEK_API_KEY}"}
    payload = {
        "model": "deepseek-chat",
        "messages": messages
    }
    try:
        response = httpx.post(
            f"{DEESEEK_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except httpx.ReadTimeout:
        return "⚠️ DeepSeek API timeout. Try again."
    except httpx.HTTPStatusError as e:
        return f"⚠️ DeepSeek API error: {e.response.text}"


def generate_chatgpt_reply(messages: list) -> str:
    """
    messages: List of {"role": "...", "content": "..."} dicts
    """
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-5",
        "messages": messages
    }
    try:
        response = httpx.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except httpx.ReadTimeout:
        return "⚠️ ChatGPT API timeout. Try again."
    except httpx.HTTPStatusError as e:
        return f"⚠️ ChatGPT API error: {e.response.text}"
