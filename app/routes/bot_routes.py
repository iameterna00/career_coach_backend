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
CHAT_STATUS_FILE = "chat_status.json"

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

def load_chat_status():
    """Load chat status from file"""
    if os.path.exists(CHAT_STATUS_FILE):
        with open(CHAT_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_chat_status(chat_status):
    """Save chat status to file"""
    with open(CHAT_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(chat_status, f, indent=2, ensure_ascii=False)

def set_chat_closed(user_id, page_id, closed=True):
    """Set chat closed status for a user"""
    chat_status = load_chat_status()
    conv_key = f"{page_id}_{user_id}"
    if closed:
        chat_status[conv_key] = {"closed": True, "closed_at": str(os.path.getmtime(CONVERSATIONS_FILE))}
    else:
        chat_status.pop(conv_key, None)
    save_chat_status(chat_status)

def is_chat_closed(user_id, page_id):
    """Check if chat is closed for a user"""
    chat_status = load_chat_status()
    conv_key = f"{page_id}_{user_id}"
    return chat_status.get(conv_key, {}).get("closed", False)

if os.path.exists(CONVERSATIONS_FILE):
    with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
        conversations.update(json.load(f))

@bot_bp.route("/clear-conversations", methods=["POST"])
def clear_conversations():
    global conversations
    conversations = {}
    save_conversations_to_file()
    save_chat_status({})
    
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

@bot_bp.route("/conversation-history", methods=["GET"])
def get_conversation_history():
    user_id = request.args.get("user_id")
    page_id = request.args.get("page_id")
    
    if not all([user_id, page_id]):
        return jsonify({"error": "Missing user_id or page_id"}), 400
    
    conv_key = f"{page_id}_{user_id}"

    if os.path.exists(CONVERSATIONS_FILE):
        with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
            current_conversations = json.load(f)
    else:
        current_conversations = {}
    conversation = current_conversations.get(conv_key, [])
    
    def remove_json_from_content(text):
        if not text:
            return text
            
        pattern = re.compile(r"<<JSON>>(.*?)<<ENDJSON>>", re.DOTALL)
        
        cleaned_text = text
        for match in pattern.finditer(text):
            json_content = match.group(0)  
            cleaned_text = cleaned_text.replace(json_content, "")
        
        cleaned_text = re.sub(r'\n\s*\n', '\n\n', cleaned_text) 
        cleaned_text = cleaned_text.strip()
        
        return cleaned_text
    
    filtered_messages = [
        {
            "role": msg["role"],
            "content": remove_json_from_content(msg["content"])
        }
        for msg in conversation 
        if msg["role"] in ["user", "assistant"]
    ]
    

    chat_closed = is_chat_closed(user_id, page_id)
    
    return jsonify({
        "messages": filtered_messages,
        "has_history": len(filtered_messages) > 0,
        "chat_closed": chat_closed 
    })

@bot_bp.route("/careerbot-stream", methods=["GET"])
def chat_stream():
    user_id = request.args.get("user_id")
    message = request.args.get("message", "").strip()
    page_id = request.args.get("page_id")
    model = request.args.get("model", "chatgpt").lower()

    if not all([user_id, page_id]):
        return jsonify({"error": "Missing required parameters"}), 400


    if is_chat_closed(user_id, page_id):
        return jsonify({"error": "Chat is closed"}), 400

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
                if "'close_chat': True" in chunk_str or '"close_chat": true' in chunk_str:
                    try:
                        import ast
                        chunk_dict = ast.literal_eval(chunk_str)
                        
                        if isinstance(chunk_dict, dict) and chunk_dict.get("close_chat"):
                            message_content = chunk_dict.get('message', '')
                            
                            conversations[conv_key].append({
                                "role": "assistant", 
                                "content": message_content
                            })
                            save_conversations_to_file()
                            
                            set_chat_closed(user_id, page_id, True)

                            close_data = {
                                'content': message_content,
                                'close_chat': True,
                                'block_typing': True
                            }
                            yield f"data: {json.dumps(close_data)}\n\n"
                            close_chat_triggered = True
                            return  
                            
                    except (SyntaxError, ValueError, Exception) as e:
                        pass
                visible_parts = json_filter.process_chunk(chunk_str)
                for part in visible_parts:
                    if part.strip():
                        visible_response += part
                        yield f"data: {json.dumps({'content': part})}\n\n"

            if not close_chat_triggered:
                if "'close_chat': True" in full_response or '"close_chat": true' in full_response:
                    try:
                        import re
                        message_match = re.search(r"'message':\s*'([^']*)'", full_response)
                        if message_match:
                            message_content = message_match.group(1)

                            conversations[conv_key].append({"role": "assistant", "content": message_content})
                            save_conversations_to_file()
                            
                            set_chat_closed(user_id, page_id, True)

                            close_data = {
                                'content': message_content,
                                'close_chat': True,
                                'block_typing': True
                            }
                            yield f"data: {json.dumps(close_data)}\n\n"
                        else:
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