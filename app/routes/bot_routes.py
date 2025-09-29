import json
import os
import re
from flask import Blueprint, request, jsonify, Response
from typing import List, Optional
from dataclasses import dataclass, field as dc_field
from app.services.file_store import setups_by_user, save_setups, leads, save_leads, page_to_setup_map, clear_leads
from app.services.context_builder import build_context
from app.services.ai_client import generate_deepseek_reply, generate_chatgpt_reply, generate_deepseek_stream, generate_chatgpt_stream
from app.services.parser import parse_booking_confirmation

bot_bp = Blueprint("bot", __name__)
conversations = {}
blocked_users = {}
CONVERSATIONS_FILE = "conversations.json"

def clear_conversations_file():
    global conversations
    conversations = {}
    if os.path.exists(CONVERSATIONS_FILE):
        with open(CONVERSATIONS_FILE, "w", encoding="utf-8") as f:
            f.write("{}")
    print("[INFO] conversations.json cleared on server start")



def save_conversations_to_file():
    with open(CONVERSATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(conversations, f, indent=2, ensure_ascii=False)

if os.path.exists(CONVERSATIONS_FILE):
    with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
        conversations.update(json.load(f))

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


@bot_bp.route("/setup", methods=["POST"])
def save_setup():
    data = request.json
    setup = SetupModel(**data)
    user_id = setup.user_id

    setups_by_user[user_id] = data
    page_to_setup_map[setup.page_id] = data
    save_setups()

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


@bot_bp.route("/leads", methods=["GET"])
def get_all_leads():
    return jsonify(leads)

@bot_bp.route("/clear-leads", methods=["POST"])
def clear_leads_endpoint():
    try:
        clear_leads()
        return jsonify({"status": "ok", "message": "All leads cleared"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    

@bot_bp.route("/carrerbot", methods=["POST"])
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
    user_message = request_obj.message.strip()

    setup = page_to_setup_map.get(page_id)
    if not setup:
        return jsonify({"reply": "Please complete your business setup first."})

    conv_key = f"{page_id}_{sender_id}"
    
    if blocked_users.get(conv_key):
        return jsonify({
            "reply": "Thank you for chatting with us! This conversation is now closed.",
            "close_chat": True
        })

    if conv_key not in conversations:
        system_prompt = build_context(setup)
        conversations[conv_key] = [{"role": "system", "content": system_prompt}]

        if not user_message:
            if model == "deepseek":
                welcome_msg = generate_deepseek_reply(conversations[conv_key])
            else:
                welcome_msg = generate_deepseek_reply(conversations[conv_key])

            # Check if it's a close_chat response (dict)
            if isinstance(welcome_msg, dict) and welcome_msg.get("close_chat"):
                blocked_users[conv_key] = True
                conversations[conv_key].append({"role": "assistant", "content": welcome_msg["message"]})
                save_conversations_to_file()
                # Return the dict directly as JSON
                return jsonify(welcome_msg)

            if "<<JSON>>" in welcome_msg and "<<ENDJSON>>" in welcome_msg:
                welcome_msg = re.sub(r"<<JSON>>.*?<<ENDJSON>>", "", welcome_msg, flags=re.DOTALL).strip()

            conversations[conv_key].append({"role": "assistant", "content": welcome_msg})
            save_conversations_to_file()
            return jsonify({"reply": welcome_msg})

    elif not user_message:
        last_assistant_msg = next(
            (msg for msg in reversed(conversations[conv_key]) if msg.get("role") == "assistant"),
            None
        )
        
        if last_assistant_msg:
            content = last_assistant_msg.get("content", "")
            if "<<JSON>>" in content and "<<ENDJSON>>" in content:
                content = re.sub(r"<<JSON>>.*?<<ENDJSON>>", "", content, flags=re.DOTALL).strip()
            return jsonify({"reply": content})
        else:
            return jsonify({"reply": ""})
        
    if user_message:
        conversations[conv_key].append({"role": "user", "content": user_message})
        save_conversations_to_file()

        messages = conversations[conv_key].copy()

        if model == "deepseek":
            bot_reply = generate_deepseek_reply(messages)
        else:
            bot_reply = generate_chatgpt_reply(messages)

        # Check if it's a close_chat response (dict)
        if isinstance(bot_reply, dict) and bot_reply.get("close_chat"):
            blocked_users[conv_key] = True
            conversations[conv_key].append({"role": "assistant", "content": bot_reply["message"]})
            save_conversations_to_file()
            # Return the dict directly as JSON
            return jsonify(bot_reply)

        # Handle normal string response
        conversations[conv_key].append({"role": "assistant", "content": bot_reply})
        save_conversations_to_file()

        confirmed = parse_booking_confirmation(bot_reply)
        if confirmed:
            confirmed["user_id"] = sender_id
            confirmed["page_id"] = page_id
            existing_lead = next(
                (l for l in leads if l["user_id"] == sender_id and l["page_id"] == page_id), None
            )
            if existing_lead:
                existing_lead.update(confirmed)
            else:
                leads.append(confirmed)
            save_leads()

        bot_reply_visible = re.sub(r"<<JSON>>.*?<<ENDJSON>>", "", bot_reply, flags=re.DOTALL).strip()
        return jsonify({"reply": bot_reply_visible})

    return jsonify({"reply": ""})
 

@bot_bp.route("/careerbot-stream", methods=["GET"])
def chat_stream():
    user_id = request.args.get("user_id")
    message = request.args.get("message", "").strip()
    page_id = request.args.get("page_id")
    model = request.args.get("model", "chatgpt").lower()

    if not all([user_id, page_id]):
        return jsonify({"error": "Missing required parameters"}), 400

    setup = page_to_setup_map.get(page_id)
    if not setup:
        return jsonify({"error": "Setup not found"}), 404

    conv_key = f"{page_id}_{user_id}"
    
    if conv_key not in conversations:
        system_prompt = build_context(setup)
        conversations[conv_key] = [{"role": "system", "content": system_prompt}]
        save_conversations_to_file()

    def generate_stream():
        try:
            if message:
                conversations[conv_key].append({"role": "user", "content": message})
                save_conversations_to_file()

            # Choose stream generator
            if model == "deepseek":
                stream_generator = generate_deepseek_stream(conversations[conv_key])
            else:
                stream_generator = generate_chatgpt_stream(conversations[conv_key])

            full_response = ""
            visible_response = ""
            close_chat_triggered = False

            class JSONFilterState:
                def __init__(self):
                    self.buffer = ""
                    self.in_json = False
                    self.json_start_marker = "<<JSON>>"
                    self.json_end_marker = "<<ENDJSON>>"
                    self.start_marker_len = len(self.json_start_marker)
                    self.end_marker_len = len(self.json_end_marker)

                def process_chunk(self, chunk):
                    self.buffer += chunk
                    visible_parts = []

                    while self.buffer:
                        if not self.in_json:
                            start_pos = self.buffer.find(self.json_start_marker)
                            if start_pos != -1:
                                if start_pos > 0:
                                    visible_parts.append(self.buffer[:start_pos])
                                self.buffer = self.buffer[start_pos + self.start_marker_len:]
                                self.in_json = True
                            else:
                                if len(self.buffer) >= self.start_marker_len:
                                    visible_parts.append(self.buffer)
                                    self.buffer = ""
                                else:
                                    if self.json_start_marker.startswith(self.buffer):
                                        break
                                    else:
                                        visible_parts.append(self.buffer)
                                        self.buffer = ""
                                break
                        else:
                            end_pos = self.buffer.find(self.json_end_marker)
                            if end_pos != -1:
                                self.buffer = self.buffer[end_pos + self.end_marker_len:]
                                self.in_json = False
                            else:
                                if len(self.buffer) >= self.end_marker_len:
                                    self.buffer = ""
                                else:
                                    break
                    return visible_parts

                def get_remaining_visible(self):
                    if self.buffer and not self.in_json:
                        return self.buffer
                    return ""

            json_filter = JSONFilterState()

            for chunk in stream_generator:

                chunk_str = str(chunk)
                full_response += chunk_str
                print(chunk_str, end="", flush=True)

                # Check if this is a close_chat response (as string)
                if "'close_chat': True" in chunk_str or '"close_chat": true' in chunk_str:
                    try:
                        # Convert string representation to Python dict
                        import ast
                        chunk_dict = ast.literal_eval(chunk_str)
                        
                        if isinstance(chunk_dict, dict) and chunk_dict.get("close_chat"):
                            message_content = chunk_dict.get('message', '')
                            
                            # Save only the message content to conversation
                            conversations[conv_key].append({
                                "role": "assistant", 
                                "content": message_content  # Only save the message, not the whole dict
                            })
                            save_conversations_to_file()
                            
                            # Send proper JSON to frontend
                            close_data = {
                                'content': message_content,
                                'close_chat': True,
                                'block_typing': True
                            }
                            yield f"data: {json.dumps(close_data)}\n\n"
                            close_chat_triggered = True
                            return  # stop streaming immediately
                            
                    except (SyntaxError, ValueError, Exception) as e:
                        # Continue with normal text processing
                        pass

                # Handle normal text chunks
                visible_parts = json_filter.process_chunk(chunk_str)
                for part in visible_parts:
                    if part.strip():
                        visible_response += part
                        yield f"data: {json.dumps({'content': part})}\n\n"

            if not close_chat_triggered:
                # After stream completes, check if full_response contains close_chat
                if "'close_chat': True" in full_response or '"close_chat": true' in full_response:
                    try:
                        import re
                        # Extract message content using regex
                        message_match = re.search(r"'message':\s*'([^']*)'", full_response)
                        if message_match:
                            message_content = message_match.group(1)
                           
                            # Save only the message content
                            conversations[conv_key].append({"role": "assistant", "content": message_content})
                            save_conversations_to_file()
                            
                            # Send close_chat signal
                            close_data = {
                                'content': message_content,
                                'close_chat': True,
                                'block_typing': True
                            }
                            yield f"data: {json.dumps(close_data)}\n\n"
                        else:
                            # Fallback: save full response
                            conversations[conv_key].append({"role": "assistant", "content": full_response})
                            save_conversations_to_file()
                    except Exception as e:
                        conversations[conv_key].append({"role": "assistant", "content": full_response})
                        save_conversations_to_file()
                else:
                    # Normal response handling
                    remaining_visible = json_filter.get_remaining_visible()
                    if remaining_visible:
                        visible_response += remaining_visible
                        yield f"data: {json.dumps({'content': remaining_visible})}\n\n"

                    conversations[conv_key].append({"role": "assistant", "content": full_response})
                    save_conversations_to_file()

                    # Extract leads from full response (JSON)
                    confirmed = parse_booking_confirmation(full_response)
                    if confirmed:
                        confirmed["user_id"] = user_id
                        confirmed["page_id"] = page_id
                        existing_lead = next((l for l in leads if l["user_id"] == user_id and l["page_id"] == page_id), None)
                        if existing_lead:
                            existing_lead.update(confirmed)
                        else:
                            leads.append(confirmed)
                        save_leads()

            yield "data: [DONE]\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(generate_stream(), mimetype="text/event-stream")