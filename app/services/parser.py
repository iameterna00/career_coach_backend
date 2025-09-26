import re
import json

def parse_booking_confirmation(text: str) -> dict:
    """
    Extract JSON from <<JSON>> ... <<ENDJSON>> safely.
    Returns a dictionary with non-empty fields.
    If no JSON is found or parsing fails, returns empty dict.
    """
    pattern = re.compile(r"<<JSON>>(.*?)<<ENDJSON>>", re.DOTALL)
    match = pattern.search(text)
    data = {}

    if match:
        content = match.group(1).strip()

        # Cleanup common issues
        content = re.sub(r'^\s*,+', '', content)  # leading commas
        content = re.sub(r',+\s*$', '', content)  # trailing commas
        content = re.sub(r',(\s*})', r'\1', content)  # trailing comma before }

        # Ensure braces
        if not content.startswith("{"):
            content = "{" + content
        if not content.endswith("}"):
            content = content + "}"

        # Remove repeated braces
        content = re.sub(r'^\{+', '{', content)
        content = re.sub(r'\}+$', '}', content)

        # Remove line breaks
        content = re.sub(r'\s*\n\s*', '', content)

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Parsing failed → keep empty dict
            data = {}

    # Keep only non-empty fields
    data = {k: v for k, v in data.items() if str(v).strip() != ""}

    return data
