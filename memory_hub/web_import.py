"""Read existing Gemini/Yuanbao exports using the original Chat RAG chunker."""
from datetime import datetime
from pathlib import Path
from . import web_chunks, web_export_common

LEGACY_MODEL = "Qwen/Qwen3-Embedding-8B"

def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10_000_000_000 else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return None

def web_records(platform, config=None):
    root = web_export_common.export_root(platform, config)
    directory = root / "browser-export-json-md" / "json"
    if not directory.is_dir():
        raise FileNotFoundError(f"Web export directory unavailable: {directory}")
    owners = web_chunks.load_gemini_owner_map(root / "owner-classification-draft" / "classification-draft.json")
    latest = {}
    for path in sorted(directory.glob("*.json")):
        data = web_export_common.read_json(path)
        cid = str(data.get("id") or data.get("url") or path.stem)
        version = timestamp(data.get("exportedAt")) or path.stat().st_mtime
        if cid not in latest or version > latest[cid][0]:
            latest[cid] = (version, path, data)
    records = []
    for cid, (_, path, data) in latest.items():
        seen = set()
        owner = owners.get(path.name, {}).get("Owner", "")
        chunks = web_chunks.build_conversation_chunks(platform=platform, file=path, record=data,
            owner=owner, max_chars=3600, overlap_chars=350, existing_chunk_ids={})
        for chunk in chunks:
            # Deduplicate repeated exports, not identical utterances in different conversations.
            key = (chunk["group_index"], chunk["text"])
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "kind": "web_chat_chunk", "title": chunk["title"], "body": chunk["text"],
                "locator": str(path), "conversation_id": cid, "sequence": chunk["chunk_index"],
                "updated_at": timestamp(data.get("lastRepliedAt")),
                "metadata": {"owner": owner, "url": chunk["url"], "group_index": chunk["group_index"],
                    "chunk_index": chunk["chunk_index"], "roles": chunk["roles"],
                    "source_format": "web_export", "embedding_model": LEGACY_MODEL,
                    "exported_at": data.get("exportedAt"), "time_basis": "lastRepliedAt" if data.get("lastRepliedAt") else "unknown"},
            })
    return records
