import json
import os
from typing import Any, Dict

class LocalStateStore:
    def __init__(self, filepath="state_store.json"):
        self.filepath = filepath

    def load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_state(self, state_dict: Dict[str, Any]):
        with open(self.filepath, "w") as f:
            json.dump(state_dict, f, indent=2)

    def update_field(self, key: str, value: Any):
        state = self.load_state()
        state[key] = value
        self.save_state(state)
