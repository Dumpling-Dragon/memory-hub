"""Opt-in OpenAI-compatible retrieval, embeddings, reranking, and cited answers."""
from __future__ import annotations

import json
import math
import heapq
import threading
import os
import re
import struct
from http.client import IncompleteRead
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config
from .db import embedding_candidates, embedding_pending_count, save_embedding, search, vectors_for_model, models_for_search, get_document
from .locking import try_lock, unlock


class AIConfigError(RuntimeError):
    pass


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:Bearer|Basic)\s+[^\s,'\";]+"),
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password|authorization)['\"]?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"),
)
_SEMANTIC_SLOT = threading.BoundedSemaphore(1)


def redact(text: str) -> str:
    """Keep local source intact but never intentionally export obvious credentials."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return text


def outbound_text(config: Config, text: str) -> str:
    return text if config.full_memory_mode else redact(text)


def _section(config: Config, name: str) -> dict[str, Any]:
    section = getattr(config, name) or {}
    if not section.get("enabled"):
        raise AIConfigError(f"{name} is disabled in %LOCALAPPDATA%\\MemoryHub\\config.json")
    if not section.get("base_url") or not section.get("model"):
        raise AIConfigError(f"{name}.base_url and {name}.model are required")
    return section


def _post(section: dict[str, Any], path: str, payload: dict) -> dict:
    key_name = section.get("api_key_env", "")
    key = os.environ.get(key_name) if key_name else None
    # A running desktop process does not inherit a user variable written after it started.
    # Read the same user-scoped environment value on Windows without duplicating it in config.
    if not key and key_name and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
                key = winreg.QueryValueEx(environment, key_name)[0]
        except OSError:
            pass
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    base = section["base_url"].rstrip("/")
    request = Request(base + path, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=float(section.get("timeout_seconds", 60))) as response:
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise AIConfigError("model response must be an object")
            return data
    except (HTTPError, URLError, TimeoutError, IncompleteRead, UnicodeError, json.JSONDecodeError) as error:
        raise AIConfigError(f"model request failed: {error}") from error


def embed_texts(config: Config, texts: list[str], model: str | None = None) -> list[list[float]]:
    section = _section(config, "embedding")
    data = _post(section, "/embeddings", {"model": model or section["model"], "input": texts})
    try:
        rows = data["data"]
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise ValueError("wrong input count")
        if sorted(row["index"] for row in rows) != list(range(len(texts))):
            raise ValueError("invalid indices")
        vectors = [row["embedding"] for row in sorted(rows, key=lambda row: row["index"])]
        dimensions = len(vectors[0]) if vectors else 0
        for vector in vectors:
            if not isinstance(vector, list) or not dimensions or len(vector) != dimensions:
                raise ValueError("inconsistent dimensions")
            if any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 3.4e38 for value in vector):
                raise ValueError("invalid vector value")
            if not any(vector):
                raise ValueError("zero vector")
        return vectors
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise AIConfigError("invalid embedding response") from error


def sync_embeddings(config: Config, limit: int = 100) -> dict:
    handle = try_lock("embedding")
    if handle is None:
        raise AIConfigError("An embedding batch is already running; try again later.")
    try:
        return _sync_embeddings(config, limit)
    finally:
        unlock(handle)


def _sync_embeddings(config: Config, limit: int) -> dict:
    section = _section(config, "embedding")
    rows = embedding_candidates(section["model"], limit)
    if not rows:
        return {"embedded": 0, "remaining": 0, "model": section["model"]}
    max_chars = int(section.get("max_input_chars", 6000))
    texts = [outbound_text(config, (row["title"] + "\n" + row["body"])[:max_chars]) for row in rows]
    vectors = embed_texts(config, texts)
    saved = 0
    for row, vector in zip(rows, vectors):
        saved += save_embedding(row["id"], row["content_hash"], section["model"], struct.pack(f"<{len(vector)}f", *vector), len(vector))
    return {"embedded": saved, "remaining": embedding_pending_count(section["model"]), "model": section["model"]}


def _cosine(left: list[float], blob: bytes, dimensions: int) -> float:
    if len(left) != dimensions or len(blob) != dimensions * 4:
        return -1.0
    right = struct.unpack(f"<{dimensions}f", blob)
    if not all(math.isfinite(value) for value in right):
        return -1.0
    denom = math.sqrt(sum(value * value for value in left) * sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right)) / denom if denom else -1.0


def semantic_search(config: Config, query: str, limit: int = 12) -> list[dict]:
    if not _SEMANTIC_SLOT.acquire(blocking=False):
        raise AIConfigError("Semantic search is busy; using local keyword results.")
    try:
        return _semantic_search(config, query, limit)
    finally:
        _SEMANTIC_SLOT.release()


def _semantic_search(config: Config, query: str, limit: int) -> list[dict]:
    section = _section(config, "embedding")
    merged = {}
    for model in models_for_search(section["model"]):
        try:
            vector = embed_texts(config, [outbound_text(config, query)], model=model)[0]
        except AIConfigError:
            continue  # A failed provider partition must not hide the other model's hits.
        scored = []
        for row in vectors_for_model(model):
            score = _cosine(vector, row.pop("vector"), row.pop("dimensions"))
            if score > 0:
                candidate = (score, row["id"])
                if len(scored) < limit:
                    heapq.heappush(scored, candidate)
                elif candidate > scored[0]:
                    heapq.heapreplace(scored, candidate)
        for rank, (score, document_id) in enumerate(sorted(scored, reverse=True), 1):
            row = get_document(document_id)
            if row is None:
                continue
            row["cosine_score"] = score
            row["excerpt"] = row["body"][:360]
            # Compare ranks, never raw cosine scores from different embedding spaces.
            previous = merged.get(row["id"], {}).get("semantic_score", 0)
            row["semantic_score"] = previous + 1 / (60 + rank)
            merged[row["id"]] = row
    return sorted(merged.values(), key=lambda row: row["semantic_score"], reverse=True)[:limit]


def retrieve(config: Config, query: str, limit: int = 12) -> list[dict]:
    # Local FTS always supplies a usable result set, even if an optional provider is unavailable.
    rows = search(query, limit=max(limit * 3, 24))
    try:
        semantic = semantic_search(config, query, limit=limit)
    except AIConfigError:
        return rerank(config, query, rows, limit)
    merged = {row["id"]: row for row in rows}
    for row in semantic:
        if row["id"] in merged:
            merged[row["id"]]["semantic_score"] = row["semantic_score"]
        else:
            merged[row["id"]] = row
    candidates = sorted(merged.values(), key=lambda row: row.get("semantic_score", 0), reverse=True)[:max(limit * 2, 20)]
    return rerank(config, query, candidates, limit)


def rerank(config: Config, query: str, rows: list[dict], limit: int) -> list[dict]:
    """Optional Cohere/Jina-style /rerank endpoint. Provider failure never removes FTS results."""
    section = getattr(config, "reranker", None) or {}
    if not section.get("enabled") or not rows:
        return rows[:limit]
    try:
        section = _section(config, "reranker")
        data = _post(section, section.get("path", "/rerank"), {
            "model": section["model"], "query": outbound_text(config, query),
            "documents": [outbound_text(config, (row["title"] + "\n" + row["body"])[:int(section.get("max_input_chars", 4000))]) for row in rows],
            "top_n": limit,
        })
        ordered = []
        for item in data.get("results", []):
            index = item.get("index")
            if isinstance(index, int) and 0 <= index < len(rows):
                row = rows[index]
                row["rerank_score"] = item.get("relevance_score", item.get("score", 0))
                ordered.append(row)
        return ordered[:limit] or rows[:limit]
    except (AIConfigError, TypeError, ValueError, AttributeError):
        return rows[:limit]


def ask(config: Config, question: str, limit: int = 10) -> dict:
    rows = retrieve(config, question, limit)
    if not rows:
        return {"answer": "没有找到可用的本地记录。", "citations": [], "results": []}
    section = _section(config, "ai")
    context = "\n\n".join(
        f"[{index}] {row['title']} | {row.get('source', '')} | {row.get('project_title') or ''}\n{row['body'][:3000]}"
        for index, row in enumerate(rows, 1)
    )
    system = "你是本地工作记忆助手。仅依据给出的记录回答；不确定时明确说明。每个事实后用 [编号] 引用。" if config.full_memory_mode else "你是本地工作记忆助手。仅依据给出的记录回答；不确定时明确说明。每个事实后用 [编号] 引用。不要输出密钥、令牌或认证信息。"
    message = outbound_text(config, f"问题：{question}\n\n记录：\n{context}")
    data = _post(section, "/chat/completions", {"model": section["model"], "temperature": 0.2, "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}]})
    try:
        answer = data["choices"][0]["message"]["content"]
        if not isinstance(answer, str):
            raise TypeError("non-text answer")
    except (KeyError, IndexError, TypeError) as error:
        raise AIConfigError("chat response has no message content") from error
    return {"answer": answer, "citations": [{"number": index, "id": row["id"], "title": row["title"], "source": row["source"]} for index, row in enumerate(rows, 1)], "results": rows}
