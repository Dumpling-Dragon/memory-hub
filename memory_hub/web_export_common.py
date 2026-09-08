"""Paths for the existing web exporters; their raw exports stay where they are."""
import json
from pathlib import Path
from .config import WORKSPACE, load_config

def workspace_root():
    return WORKSPACE.parent

def export_root(platform, config=None):
    config = config or load_config()
    configured = (config.web_export_roots or {}).get(platform)
    if configured:
        return Path(configured).expanduser()
    return WORKSPACE / f"2026-06-08_{platform}聊天批量导出"

def default_gemini_json_dir():
    return export_root("gemini") / "browser-export-json-md" / "json"

def default_yuanbao_json_dir():
    return export_root("yuanbao") / "browser-export-json-md" / "json"

def default_gemini_owner_path():
    return export_root("gemini") / "owner-classification-draft" / "classification-draft.json"

def default_yuanbao_owner_path():
    return export_root("yuanbao") / "owner-classification-draft" / "classification-draft.json"

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
