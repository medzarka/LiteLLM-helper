import os
import json

DATA_DIR = os.environ.get('DATA_DIR', 'data')
HERMES_AGENTS_FILE = os.path.join(DATA_DIR, 'hermes_agents.json')

def load_hermes_agents():
    """
    Returns a dictionary mapping hermes task names (e.g. 'hermes-vision')
    to the selected model ID (int).
    """
    if not os.path.exists(HERMES_AGENTS_FILE):
        return {}
    try:
        with open(HERMES_AGENTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Ensure values are ints where possible
            return {k: int(v) if str(v).isdigit() else v for k, v in data.items()}
    except Exception as e:
        print(f"Error loading hermes agents: {e}")
        return {}

def save_hermes_agents(agents_data):
    """
    Saves the dictionary mapping to hermes_agents.json.
    """
    os.makedirs(os.path.dirname(HERMES_AGENTS_FILE), exist_ok=True)
    try:
        with open(HERMES_AGENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(agents_data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving hermes agents: {e}")
        return False
