import os
import httpx
import json
from dotenv import load_dotenv
from openai import OpenAI

# Load .env file
load_dotenv()

# Read API keys from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEESEEK_API_KEY = os.getenv("DEESEEK_API_KEY")
DEESEEK_BASE_URL = "https://api.deepseek.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"

# Validate keys
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set in .env")

if not DEESEEK_API_KEY:
    raise RuntimeError("DEESEEK_API_KEY not set in .env")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------
# OpenAI Function Calling
# -------------------
functions = [
    {
        "name": "close_chat",
        "description": "Closes the chat when use say he is bba student",
        "parameters": {
            "type": "object",
            "properties": {
                "end_conversation": {"type": "string", "description": "End with a polite message"}
            },
            "required": ["end_conversation"]
        }
    }
]

def handle_close_chat(reason: str) -> dict:
    print(f"❌ Chat closed: {reason}")
    # Return a special payload for frontend
    return {
        "function": "close_chat",
        "message":reason,
        "block_typing": True ,
        "close_chat": True
    }


# -------------------
# DeepSeek Functions
# -------------------
def generate_deepseek_reply(messages: list) -> str:
    headers = {"Authorization": f"Bearer {DEESEEK_API_KEY}"}
    payload = {"model": "deepseek-chat", "messages": messages}

    try:
        response = httpx.post(f"{DEESEEK_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=60.0)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"⚠️ DeepSeek API error: {str(e)}"

def generate_deepseek_stream(messages: list):
    headers = {"Authorization": f"Bearer {DEESEEK_API_KEY}"}
    payload = {"model": "deepseek-chat", "messages": messages, "stream": True, "temperature": 0.7}

    try:
        with httpx.stream(
            "POST",
            f"{DEESEEK_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=None
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):  # <-- FIX HERE
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        json_data = json.loads(data)
                        delta = json_data["choices"][0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                    except Exception:
                        continue

    except Exception as e:
        yield f"⚠️ DeepSeek streaming error: {str(e)}"

# -------------------
# OpenAI Functions with Function Calling
# -------------------
def generate_chatgpt_reply(messages: list) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=messages,
            functions=functions,
            function_call="auto"
        )

        choice = response.choices[0]

        # Normal content
        if choice.message.content:
            return choice.message.content.strip()

        # Function call
        if choice.message.function_call:
            fn_name = choice.message.function_call.name
            fn_args = json.loads(choice.message.function_call.arguments)
            if fn_name == "close_chat":
                return handle_close_chat(fn_args["end_conversation"])

        return "⚠️ No usable response"

    except Exception as e:
        return f"⚠️ ChatGPT API error: {str(e)}"

def generate_chatgpt_stream(messages: list):
    try:
        stream = client.chat.completions.create(
            model="gpt-5",
            messages=messages,
            stream=True,
       
            functions=functions,
            function_call="auto"
        )

        collected_function = {"name": None, "arguments": ""}
        for event in stream:
            if len(event.choices) == 0:
                continue
            delta = event.choices[0].delta
            if not delta:
                continue

            # Normal text
            if delta.content:
                print(delta.content, end="", flush=True)
                yield delta.content

            # Function call streaming
            if delta.function_call:
                if delta.function_call.name:
                    collected_function["name"] = delta.function_call.name
                if delta.function_call.arguments:
                    collected_function["arguments"] += delta.function_call.arguments

        # Handle full function call at end
        if collected_function["name"] == "close_chat":
            args = json.loads(collected_function["arguments"])
            yield f"\n{handle_close_chat(args['end_conversation'])}\n"

    except Exception as e:
        yield f"⚠️ ChatGPT streaming error: {str(e)}"
