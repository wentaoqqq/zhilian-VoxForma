"""Small declarative rule packs. No plugin scripts or plugin service process."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).with_name("rulepacks")


def get_plugin(plugin_id: str) -> dict:
    if not re.fullmatch(r"[a-z0-9-]{1,60}", plugin_id):
        raise ValueError("Unknown production rule pack.")
    path = ROOT / (plugin_id + ".json")
    if not path.is_file():
        raise ValueError("Unknown production rule pack.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_plugins() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(ROOT.glob("*.json"))]
