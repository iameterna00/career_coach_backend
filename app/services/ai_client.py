import os
import httpx
import json
from dotenv import load_dotenv
from openai import OpenAI
import re


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
        "description": "Close the chat once all fields are filled or the user has answered all questions.",
        "parameters": {
            "type": "object",
            "properties": {
                "end_conversation": {"type": "string", "description": "End with a polite message and provide an overall strengths integration for user, which will be the core foundation for generating their Career Planning Report"}
            },
            "required": ["end_conversation"]
        }
    }
]

def handle_close_chat(reason: str) -> dict:
    print(f"❌ Chat closed: {reason}")
    
    # Remove JSON parts from the message content
    def remove_json_from_content(text):
        if not text:
            return text
            
        pattern = re.compile(r"<<JSON>>(.*?)<<ENDJSON>>", re.DOTALL)
        
        cleaned_text = text
        for match in pattern.finditer(text):
            json_content = match.group(0)  # Get the entire JSON block including markers
            cleaned_text = cleaned_text.replace(json_content, "")
        
        # Clean up any extra whitespace
        cleaned_text = re.sub(r'\n\s*\n', '\n\n', cleaned_text) 
        cleaned_text = cleaned_text.strip()
        
        return cleaned_text
    
    # Clean the reason message to remove JSON
    cleaned_message = remove_json_from_content(reason)
    
    return {
        "function": "close_chat",
        "message": cleaned_message,
        "block_typing": True,
        "close_chat": True
    }

# -------------------
# DeepSeek Functions
# -------------------
def generate_deepseek_reply(messages: list) -> str:
    headers = {"Authorization": f"Bearer {DEESEEK_API_KEY}"}
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "functions": functions,      # reuse the OpenAI functions
        "function_call": "auto"
    }

    try:
        response = httpx.post(f"{DEESEEK_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=60.0)
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]["message"]

        # Normal content
        if "content" in choice and choice["content"]:
            return choice["content"].strip()

        # Function call (reuse OpenAI handler)
        if "function_call" in choice:
            fn_name = choice["function_call"]["name"]
            fn_args = json.loads(choice["function_call"]["arguments"])
            if fn_name == "close_chat":
                return handle_close_chat(fn_args["end_conversation"])

        return "⚠️ No usable DeepSeek response"

    except Exception as e:
        return f"⚠️ DeepSeek API error: {str(e)}"
def generate_deepseek_stream(messages: list):
    headers = {"Authorization": f"Bearer {DEESEEK_API_KEY}"}
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "stream": True,
        "functions": functions,
        "function_call": "auto",
        "temperature": 0.7
    }

    collected_function = {"name": None, "arguments": ""}

    try:
        with httpx.stream("POST", f"{DEESEEK_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=None) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                json_data = json.loads(data)
                delta = json_data["choices"][0].get("delta", {})
                if "content" in delta:
                    yield delta["content"]
                if "function_call" in delta:
                    if delta["function_call"].get("name"):
                        collected_function["name"] = delta["function_call"]["name"]
                    if delta["function_call"].get("arguments"):
                        collected_function["arguments"] += delta["function_call"]["arguments"]

        # Handle final function call
        if collected_function["name"] == "close_chat":
            args = json.loads(collected_function["arguments"])
            yield f"\n{handle_close_chat(args['end_conversation'])}\n"

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
