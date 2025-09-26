from pathlib import Path
import json

import os

# --- Helper Functions ---
def load_json(path, default=None):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default
    return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# --- Data Loading ---
setups_by_user = load_json(Path("data/setups.json"), default={})
leads = load_json(Path("data/leads.json"), default=[])

page_to_setup_map = {}

def build_page_map():
    global page_to_setup_map
    page_to_setup_map = {}
    for user_id, setup in setups_by_user.items():
        if not isinstance(setup, dict):
            continue
        page_id = setup.get("page_id")
        if page_id:
            page_to_setup_map[page_id] = {**setup, "user_id": user_id}

# Call on load
build_page_map()

# --- Save Helpers ---
def save_setups():
    save_json(Path("data/setups.json"), setups_by_user)
    build_page_map()  # Keep map in sync

def save_leads():
    save_json(Path("data/leads.json"), leads)
# --- Clear Leads Helper ---


def clear_leads():
    global leads
    leads = []

    leads_file = Path("data/leads.json")
    if leads_file.exists():
        os.remove(leads_file)  # delete the file