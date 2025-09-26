import json
import os
from flask import Blueprint, request, jsonify
from typing import List, Optional
from dataclasses import dataclass, field as dc_field
from app.services.file_store import setups_by_user, save_setups, leads, save_leads, page_to_setup_map, clear_leads
from app.services.context_builder import build_context
from app.services.ai_client import generate_deepseek_reply, generate_chatgpt_reply
from app.services.parser import parse_booking_confirmation
import re

bot_bp = Blueprint("bot", __name__)
conversations = {}
CONVERSATIONS_FILE = "conversations.json"

# --- Helper to save conversations to file ---
def save_conversations_to_file():
    with open(CONVERSATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(conversations, f, indent=2, ensure_ascii=False)

# --- Load conversations from file on startup ---
if os.path.exists(CONVERSATIONS_FILE):
    with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
        conversations.update(json.load(f))

# --- Data classes ---
@dataclass
class Service:
    name: Optional[str] = ""
    price: Optional[str] = "0"
    negotiable: Optional[str] = "0"

@dataclass
class SetupModel:
    page_id: str
    user_id: str
    platform: Optional[str] = None
    business_name: Optional[str] = ""
    business_address: Optional[str] = ""
    offerings: Optional[str] = ""
    business_hours: Optional[str] = ""
    goalType: Optional[str] = ""
    field: List[str] = dc_field(default_factory=list)
    toneAndVibe: Optional[List[str]] = dc_field(default_factory=list)
    additionalPrompt: Optional[str] = ""
    followUps: Optional[str] = ""
    agent_name: Optional[str] = ""
    services: List[Service] = dc_field(default_factory=list)

@dataclass
class ChatRequest:
    user_id: str
    message: str
    page_id: str
    model: Optional[str] = "chatgpt"
    modelConfig: Optional[dict] = None

# --- Routes ---
@bot_bp.route("/setup", methods=["POST"])
def save_setup():
    data = request.json
    setup = SetupModel(**data)
    user_id = setup.user_id
    setups_by_user[user_id] = data
    page_to_setup_map[setup.page_id] = data
    save_setups()

    # Clear existing conversations for this page
    keys_to_delete = [k for k in conversations if k.startswith(setup.page_id)]
    for k in keys_to_delete:
        del conversations[k]

    save_conversations_to_file()
    return jsonify({"status": "ok", "message": "Setup saved"})

@bot_bp.route("/setup/<user_id>", methods=["GET"])
def get_setup_for_user(user_id):
    user_data = setups_by_user.get(user_id)
    if not user_data:
        return jsonify({"error": "User data not found"}), 404
    return jsonify(user_data)

@bot_bp.route("/clear-conversations", methods=["POST"])
def clear_conversations():
    global conversations
    conversations = {}
    save_conversations_to_file()
    return jsonify({"status": "ok", "message": "All conversations cleared"})

@bot_bp.route("/clinicchat", methods=["POST"])
def chat():
    data = request.json
    request_obj = ChatRequest(
        user_id=data.get("user_id"),
        message=data.get("message", ""),
        page_id=data.get("page_id"),
        model=data.get("model", "chatgpt"),
        modelConfig=data.get("modelConfig")
    )

    page_id = request_obj.page_id
    sender_id = request_obj.user_id
    model = request_obj.model.lower() if request_obj.model else "chatgpt"

    setup = page_to_setup_map.get(page_id)
    if not setup:
        return jsonify({"reply": "Please complete your business setup first."})

    conv_key = f"{page_id}_{sender_id}"
    user_message = request_obj.message.strip()

    # --- Initialize conversation dynamically using AI ---
    if conv_key not in conversations:
        conversations[conv_key] = []

        # Prompt to generate dynamic first greeting
        prompt = f"""{build_context(setup)}

User hasn't said anything yet. Generate a friendly, professional welcome message for career coaching.
"""
        if model == "deepseek":
            welcome_msg = generate_deepseek_reply(prompt)
        else:
            welcome_msg = generate_chatgpt_reply(prompt)

        conversations[conv_key].append({"role": "bot", "message": welcome_msg})
        save_conversations_to_file()

        # Return the AI-generated first greeting immediately
        if not user_message:
            return jsonify({"reply": welcome_msg})

    # --- If user message is empty, return last bot message ---
    elif not user_message:
        for msg in reversed(conversations[conv_key]):
            if msg.get("role") == "bot":
                return jsonify({"reply": msg.get("message")})

    # --- Process non-empty user messages ---
    if user_message:
        conversations[conv_key].append({"role": "user", "message": user_message})
        save_conversations_to_file()

        conversation_history = "\n".join([f"{m['role'].capitalize()}: {m['message']}" for m in conversations[conv_key]])
        prompt = f"""{build_context(setup)}

Conversation history:
{conversation_history}

Please respond as the business assistant.
"""

        if model == "deepseek":
            bot_reply = generate_deepseek_reply(prompt)
        else:
            bot_reply = generate_chatgpt_reply(prompt)

        conversations[conv_key].append({"role": "bot", "message": bot_reply})
        save_conversations_to_file()

        # Extract leads
        confirmed = parse_booking_confirmation(bot_reply)
        if confirmed:
            confirmed["user_id"] = sender_id
            confirmed["page_id"] = page_id

            existing_lead = next((l for l in leads if l["user_id"] == sender_id and l["page_id"] == page_id), None)
            if existing_lead:
                existing_lead.update(confirmed)
            else:
                leads.append(confirmed)

            save_leads()

        bot_reply_visible = re.sub(r"<<JSON>>.*?<<ENDJSON>>", "", bot_reply, flags=re.DOTALL).strip()
        return jsonify({"reply": bot_reply_visible})

    return jsonify({"reply": ""})



@bot_bp.route("/leads", methods=["GET"])
def get_all_leads():
    return jsonify(leads)

@bot_bp.route("/clear-leads", methods=["POST"])
def clear_leads_endpoint():
    try:
        clear_leads()  # call the function from file_store.py
        return jsonify({"status": "ok", "message": "All leads cleared"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
