from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from .web_export_common import (
    default_gemini_json_dir, default_gemini_owner_path,
    default_yuanbao_json_dir, default_yuanbao_owner_path,
    read_json, write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize exported chat JSON into retrieval chunks.")
    parser.add_argument("--gemini-json-dir", type=Path, default=default_gemini_json_dir())
    parser.add_argument("--yuanbao-json-dir", type=Path, default=default_yuanbao_json_dir())
    parser.add_argument("--gemini-owner-path", type=Path, default=default_gemini_owner_path())
    parser.add_argument("--yuanbao-owner-path", type=Path, default=default_yuanbao_owner_path())
    parser.add_argument("--output", type=Path, default=Path("data/chunks.jsonl"))
    parser.add_argument("--max-chars", type=int, default=3600)
    parser.add_argument("--overlap-chars", type=int, default=350)
    parser.add_argument(
        "--reuse-existing-ids-from-db",
        type=Path,
        default=None,
        help="Reuse chunk IDs from an existing SQLite index by platform/source/group/chunk to avoid re-embedding metadata-only changes.",
    )
    return parser.parse_args()


def normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    if role in {"user", "human", "你说"}:
        return "user"
    if role in {"assistant", "ai", "model", "gemini", "元宝"}:
        return "assistant"
    return role or "unknown"


def clean_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def chunk_long_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n\n", start, end), text.rfind("。", start, end), text.rfind(".", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap_chars)
    return [chunk for chunk in chunks if chunk]


def stable_id(*parts: str) -> str:
    return hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()[:16]


def load_existing_chunk_ids(db_path: Path | None) -> dict[tuple[str, str, int, int], str]:
    if not db_path or not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT platform, source_json, group_index, chunk_index, chunk_id
        FROM chunks
        WHERE platform IN ('gemini', 'yuanbao')
        """
    )
    return {
        (str(platform), str(source_json), int(group_index), int(chunk_index)): str(chunk_id)
        for platform, source_json, group_index, chunk_index, chunk_id in rows
    }


def load_gemini_owner_map(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = read_json(path)
    result = {}
    for row in rows:
        json_file = row.get("JsonFile") or row.get("jsonFile")
        if json_file:
            result[json_file] = row
    return result


def message_text(message: dict[str, Any]) -> str:
    return clean_text(message.get("text") or message.get("content") or message.get("speech") or "")


def grouped_dialogue(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = []
    current_user = ""
    assistant_parts: list[str] = []
    raw_roles: list[str] = []

    def flush() -> None:
        nonlocal current_user, assistant_parts, raw_roles
        text_parts = []
        if current_user:
            text_parts.append(f"用户：\n{current_user}")
        if assistant_parts:
            assistant_text = "\n\n".join(assistant_parts)
            text_parts.append(f"AI：\n{assistant_text}")
        if text_parts:
            groups.append({"text": "\n\n".join(text_parts), "roles": raw_roles[:]})
        current_user = ""
        assistant_parts = []
        raw_roles = []

    for message in messages:
        role = normalize_role(str(message.get("role", "")))
        text = message_text(message)
        if not text:
            continue
        if role == "user":
            flush()
            current_user = text
            raw_roles = ["user"]
        elif role == "assistant":
            assistant_parts.append(text)
            raw_roles.append("assistant")
        else:
            if current_user or assistant_parts:
                assistant_parts.append(text)
                raw_roles.append(role)
            else:
                current_user = text
                raw_roles = [role]
    flush()
    return groups


def build_conversation_chunks(
    *,
    platform: str,
    file: Path,
    record: dict[str, Any],
    owner: str,
    max_chars: int,
    overlap_chars: int,
    existing_chunk_ids: dict[tuple[str, str, int, int], str],
) -> list[dict[str, Any]]:
    title = record.get("title") or record.get("sourceListTitle") or file.stem
    messages = record.get("messages") if isinstance(record.get("messages"), list) else []
    groups = grouped_dialogue(messages)
    if not groups:
        fallback = clean_text(record.get("text") or record.get("rawText") or "")
        groups = [{"text": fallback, "roles": ["raw"]}] if fallback else []

    chunks = []
    chunk_index = 0
    for group_index, group in enumerate(groups):
        headed = f"平台：{platform}\n标题：{title}\n归属：{owner or 'unknown'}\n\n{group['text']}"
        for part_index, part in enumerate(chunk_long_text(headed, max_chars, overlap_chars)):
            reuse_key = (platform, str(file), group_index, chunk_index)
            chunk_id = existing_chunk_ids.get(reuse_key) or stable_id(platform, file.name, str(group_index), str(part_index), part[:200])
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "platform": platform,
                    "title": title,
                    "owner": owner or "",
                    "source_json": str(file),
                    "url": record.get("url") or "",
                    "conversation_id": record.get("id") or record.get("url") or file.stem,
                    "chunk_index": chunk_index,
                    "group_index": group_index,
                    "roles": group["roles"],
                    "text": part,
                }
            )
            chunk_index += 1
    return chunks


def collect_gemini(args: argparse.Namespace) -> list[dict[str, Any]]:
    owner_map = load_gemini_owner_map(args.gemini_owner_path)
    existing_chunk_ids = load_existing_chunk_ids(args.reuse_existing_ids_from_db)
    chunks = []
    if not args.gemini_json_dir.exists():
        return chunks
    for file in sorted(args.gemini_json_dir.glob("*.json")):
        record = read_json(file)
        owner = owner_map.get(file.name, {}).get("Owner", "")
        chunks.extend(
            build_conversation_chunks(
                platform="gemini",
                file=file,
                record=record,
                owner=owner,
                max_chars=args.max_chars,
                overlap_chars=args.overlap_chars,
                existing_chunk_ids=existing_chunk_ids,
            )
        )
    return chunks


def collect_yuanbao(args: argparse.Namespace) -> list[dict[str, Any]]:
    owner_map = load_gemini_owner_map(args.yuanbao_owner_path)
    existing_chunk_ids = load_existing_chunk_ids(args.reuse_existing_ids_from_db)
    chunks = []
    if not args.yuanbao_json_dir.exists():
        return chunks
    for file in sorted(args.yuanbao_json_dir.glob("*.json")):
        record = read_json(file)
        owner = owner_map.get(file.name, {}).get("Owner", "")
        chunks.extend(
            build_conversation_chunks(
                platform="yuanbao",
                file=file,
                record=record,
                owner=owner,
                max_chars=args.max_chars,
                overlap_chars=args.overlap_chars,
                existing_chunk_ids=existing_chunk_ids,
            )
        )
    return chunks


def main() -> None:
    args = parse_args()
    chunks = collect_gemini(args) + collect_yuanbao(args)
    write_jsonl(args.output, chunks)
    platforms = {}
    for chunk in chunks:
        platforms[chunk["platform"]] = platforms.get(chunk["platform"], 0) + 1
    print({"output": str(args.output), "chunks": len(chunks), "platforms": platforms})


if __name__ == "__main__":
    main()
