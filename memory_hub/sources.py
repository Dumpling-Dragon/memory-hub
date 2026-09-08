from __future__ import annotations

import base64
import gc
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import APP_DATA, Config


HOME = Path.home()
LOG = logging.getLogger(__name__)
SNAPSHOT_ROOT = APP_DATA / "sqlite-snapshots"
TEXT_EXTENSIONS = {".md", ".txt", ".py", ".ps1", ".cmd", ".bat", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini", ".csv", ".html", ".css", ".sql", ".sh"}
EXCLUDED_NAMES = {"auth.json", ".env", ".env.local", "cookies", "login data"}
LOCKFILE_NAMES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb", "poetry.lock", "uv.lock", "pipfile.lock", "cargo.lock", "composer.lock"}
MAX_FILESYSTEM_CHARACTERS = 12_000_000
EXCLUDED_PARTS = {".git", "node_modules", "cache", "code cache", "gpucache", "blob_storage", "session storage", "local storage", "__pycache__", ".venv"}
FILESYSTEM_EXCLUDED_PARTS = EXCLUDED_PARTS | {"venv", "site-packages", ".build", "_internal", ".next", ".cache", "logs", "log", "dist", "build", "backups", "sqlite-snapshots"}
_CODEX_RECORD_CACHE: dict[str, tuple[int, int, str, str, str, list[dict]]] = {}


def _safe_json_lines(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        yield index, value
                except json.JSONDecodeError:
                    continue
    except OSError:
        # An unreadable transcript is not evidence that its old records were deleted.
        raise


def _fingerprint_paths(paths: Iterable[Path]) -> str:
    """Hash cheap filesystem metadata, not file contents, for source checkpoints."""
    digest = hashlib.sha256()
    seen: set[str] = set()
    for target in paths:
        target = Path(target)
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = []
            for directory, child_dirs, filenames in os.walk(target, topdown=True, onerror=lambda _: None):
                child_dirs.sort()
                for filename in sorted(filenames):
                    candidates.append(Path(directory) / filename)
        else:
            candidates = [target]
        for path in candidates:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                stat = path.stat()
            except OSError:
                digest.update(f"missing:{key}".encode("utf-8", "replace"))
                continue
            digest.update(key.encode("utf-8", "replace"))
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
    return digest.hexdigest()


def source_fingerprint(source: str, config: Config | None = None) -> str | None:
    """Return a cheap checkpoint for one adapter; None means always reconcile."""
    appdata = Path(os.environ.get("APPDATA", ""))
    localappdata = Path(os.environ.get("LOCALAPPDATA", ""))
    if source in {"gemini", "yuanbao"}:
        from .web_export_common import export_root
        root = export_root(source, config)
        return _fingerprint_paths((root / "browser-export-json-md" / "json", root / "owner-classification-draft" / "classification-draft.json"))
    if source == "codex":
        root = HOME / ".codex"
        # The Codex state DB mtime changes for UI metadata on almost every turn.
        # Rollout/session files are the authoritative searchable bodies and are
        # enough to detect new or changed transcript content without retriggering
        # a full parse merely because the index DB was touched.
        return _fingerprint_paths((root / "session_index.jsonl", root / "sessions", root / "archived_sessions"))
    if source == "claude_code":
        return _fingerprint_paths((HOME / ".claude" / "projects", HOME / ".claude" / "transcripts"))
    if source == "hermes":
        path = localappdata / "Hermes" / "state.db"
        return _fingerprint_paths((path, Path(str(path) + "-wal")))
    if source == "workbuddy":
        root = HOME / ".workbuddy"
        return _fingerprint_paths((root / "workbuddy.db", root / "workbuddy.db-wal", root / "workbuddy.db-shm", root / "projects"))
    if source == "qoder":
        root = appdata / "Qoder" / "User"
        return _fingerprint_paths((root / "workspaceStorage", root / "globalStorage" / "state.vscdb"))
    if source == "qoderwork_cn":
        path = appdata / "QoderWork CN" / "data" / "agents.db"
        return _fingerprint_paths((path, Path(str(path) + "-wal")))
    if source == "pi":
        session_dir = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
        if session_dir:
            root = Path(session_dir)
        else:
            base = os.environ.get("PI_CODING_AGENT_DIR")
            root = Path(base) / "agent" / "sessions" if base else HOME / ".pi" / "agent" / "sessions"
        return _fingerprint_paths((root,))
    if source == "deepseek_harness":
        home = os.environ.get("DSH_HOME")
        root = Path(home) / "sessions" if home else HOME / ".dsh" / "sessions"
        return _fingerprint_paths((root,))
    if source == "zcode":
        root = HOME / ".zcode"
        return _fingerprint_paths((
            root / "v2" / "tasks-index.sqlite",
            Path(str(root / "v2" / "tasks-index.sqlite") + "-wal"),
            root / "cli" / "rollout",
        ))
    return None


def _strings(value: Any, keys: set[str] | None = None, depth: int = 0, budget: int = 24) -> list[str]:
    """Extract bounded human text from event shapes; never recurse through raw tool payloads."""
    if depth > 5 or budget <= 0:
        return []
    found: list[str] = []
    wanted = keys or {"text", "content", "message", "summary", "title", "prompt"}
    blocked = {"input", "output", "arguments", "params", "tool_calls", "tool_call", "result", "items", "reasoning"}
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if lowered in blocked or any(token in lowered for token in ("token", "password", "secret", "authorization", "cookie", "api_key")):
                continue
            if lowered in wanted:
                found.extend(_strings(child, wanted, depth + 1, budget - len(found)))
            elif lowered in {"item", "payload", "data"} and isinstance(child, (dict, list)):
                found.extend(_strings(child, wanted, depth + 1, budget - len(found)))
            if len(found) >= budget:
                break
    elif isinstance(value, list):
        for child in value[:12]:
            found.extend(_strings(child, wanted, depth + 1, budget - len(found)))
            if len(found) >= budget:
                break
    elif isinstance(value, str) and value.strip():
        found.append(value.strip()[:20_000])
    return found


def _event_record(source: str, path: Path, sequence: int, event: dict[str, Any], workspace: str | None = None, title: str | None = None) -> dict | None:
    text = "\n".join(_strings(event))
    if not text:
        return None
    event_title = title or str(event.get("title") or event.get("type") or path.stem)
    timestamp = event.get("timestamp") or event.get("ts") or event.get("updated_at")
    if isinstance(timestamp, str):
        timestamp = path.stat().st_mtime
    return {
        "kind": "message", "title": event_title[:240], "body": text[:80_000], "locator": str(path),
        "conversation_id": str(event.get("sessionId") or event.get("session_id") or event.get("id") or path.stem),
        "workspace_path": workspace, "project_title": Path(workspace).name if workspace else path.parent.name,
        "updated_at": float(timestamp) if isinstance(timestamp, (int, float)) else path.stat().st_mtime,
        "sequence": sequence, "metadata": {"event_type": event.get("type"), "source_format": "jsonl"},
    }


def codex_records() -> list[dict]:
    db_path = HOME / ".codex" / "state_5.sqlite"
    records: list[dict] = []
    if not db_path.exists():
        return records
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, rollout_path, cwd, title, updated_at FROM threads").fetchall()
    active_paths: set[str] = set()
    for row in rows:
        raw_path = str(row["rollout_path"]).replace("\\\\?\\", "")
        path = Path(raw_path)
        if not path.exists():
            continue
        cache_path = str(path).lower()
        active_paths.add(cache_path)
        try:
            stat = path.stat()
        except OSError:
            continue
        cache = _CODEX_RECORD_CACHE.get(cache_path)
        cache_matches = cache and cache[:5] == (
            stat.st_mtime_ns, stat.st_size, str(row["id"]), str(row["cwd"] or ""), str(row["title"] or "")
        )
        if cache_matches:
            cached_records = cache[5]
            if row["updated_at"] is not None:
                for cached_record in cached_records:
                    cached_record["updated_at"] = row["updated_at"]
            records.extend(cached_records)
            continue
        file_records: list[dict] = []
        for sequence, event in _safe_json_lines(path):
            record = _event_record("codex", path, sequence, event, row["cwd"], row["title"])
            if record:
                record["conversation_id"] = row["id"]
                record["updated_at"] = row["updated_at"] or record["updated_at"]
                file_records.append(record)
        _CODEX_RECORD_CACHE[cache_path] = (
            stat.st_mtime_ns, stat.st_size, str(row["id"]), str(row["cwd"] or ""), str(row["title"] or ""), file_records
        )
        records.extend(file_records)
    # Drop entries for deleted/missing sessions so a long-running service does
    # not retain stale transcript bodies forever.
    for cache_path in set(_CODEX_RECORD_CACHE) - active_paths:
        _CODEX_RECORD_CACHE.pop(cache_path, None)
    return records


def claude_records() -> list[dict]:
    root = HOME / ".claude" / "projects"
    records: list[dict] = []
    if not root.exists():
        return records
    for path in root.rglob("*.jsonl"):
        # Claude encodes a workspace path in the parent directory; event cwd takes precedence.
        inferred = str(path.parent).replace("--", ":\\", 1).replace("-", "\\") if path.parent != root else None
        for sequence, event in _safe_json_lines(path):
            workspace = event.get("cwd") or event.get("workspace") or inferred
            record = _event_record("claude_code", path, sequence, event, workspace)
            if record:
                records.append(record)
    transcript_root = HOME / ".claude" / "transcripts"
    if transcript_root.exists():
        for path in transcript_root.rglob("*.jsonl"):
            for sequence, event in _safe_json_lines(path):
                record = _event_record("claude_code", path, sequence, event)
                if record:
                    records.append(record)
    return records


def _snapshot_sqlite(source: Path) -> Path:
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="snapshot-", dir=SNAPSHOT_ROOT))
    target = temp_dir / source.name
    source_conn = None
    target_conn = None
    try:
        source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        target_conn = sqlite3.connect(target)
        source_conn.backup(target_conn)
        return target
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    finally:
        if target_conn is not None:
            target_conn.close()
        if source_conn is not None:
            source_conn.close()


@contextmanager
def _snapshot_connection(snapshot: Path):
    """Open a snapshot and always close the handle before Windows cleanup."""
    conn = sqlite3.connect(snapshot)
    try:
        yield conn
    finally:
        conn.close()


def _cleanup_snapshot(snapshot: Path) -> None:
    """Remove a copied SQLite snapshot, retrying after connection finalizers run on Windows."""
    directory = snapshot.parent
    for _ in range(3):
        gc.collect()
        try:
            shutil.rmtree(directory)
            return
        except OSError:
            time.sleep(0.2)
    LOG.warning("Could not remove SQLite snapshot directory: %s", directory)


def cleanup_stale_snapshots(max_age_seconds: float = 600) -> None:
    """Reclaim snapshots left by an interrupted previous sync without touching active ones."""
    if not SNAPSHOT_ROOT.exists():
        return
    cutoff = time.time() - max_age_seconds
    for directory in SNAPSHOT_ROOT.iterdir():
        if directory.is_dir() and directory.name.startswith("snapshot-") and directory.stat().st_mtime < cutoff:
            _cleanup_snapshot(directory / "placeholder")


def hermes_records() -> list[dict]:
    path = Path(os.environ.get("LOCALAPPDATA", "")) / "Hermes" / "state.db"
    if not path.exists():
        return []
    snapshot = _snapshot_sqlite(path)
    try:
        with _snapshot_connection(snapshot) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT m.id,m.session_id,m.role,m.content,m.timestamp,s.title,s.cwd,s.git_repo_root,s.git_branch
                   FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.active=1 AND m.content IS NOT NULL"""
            ).fetchall()
        return [{
            "kind": "message", "title": row["title"] or f"Hermes {row['role']} message", "body": row["content"][:80_000],
            "locator": str(path), "conversation_id": row["session_id"], "workspace_path": row["cwd"] or row["git_repo_root"],
            "project_title": Path(row["cwd"] or row["git_repo_root"] or "Hermes").name, "updated_at": row["timestamp"],
            "sequence": row["id"], "metadata": {"role": row["role"], "git_branch": row["git_branch"], "source_format": "sqlite_snapshot"},
        } for row in rows if row["content"].strip()]
    finally:
        _cleanup_snapshot(snapshot)


def _decode_workbuddy_directory(name: str) -> str | None:
    try:
        decoded = base64.b64decode(name + "=" * (-len(name) % 4)).decode("utf-8")
        return decoded if re.match(r"^[A-Za-z]:[\\/]", decoded) else None
    except (ValueError, UnicodeDecodeError):
        return None


def workbuddy_records() -> list[dict]:
    """Read WorkBuddy's authoritative SQLite session index plus JSONL message bodies."""
    home_root = HOME / ".workbuddy"
    db_path = home_root / "workbuddy.db"
    projects_root = home_root / "projects"
    records: list[dict] = []
    if not db_path.exists() or not projects_root.exists():
        return records
    snapshot = _snapshot_sqlite(db_path)
    try:
        with _snapshot_connection(snapshot) as conn:
            conn.row_factory = sqlite3.Row
            sessions = {
                row["id"]: dict(row) for row in conn.execute(
                    "SELECT id,cwd,title,custom_title,created_at,updated_at,status FROM sessions WHERE deleted_at IS NULL"
                ).fetchall()
            }
    finally:
        _cleanup_snapshot(snapshot)

    for path in projects_root.rglob("*.jsonl"):
        for sequence, event in _safe_json_lines(path):
            if event.get("type") != "message" or event.get("role") not in {"user", "assistant"}:
                continue
            parts = event.get("content")
            text = "\n".join(
                str(part.get("text", "")).strip() for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
            ) if isinstance(parts, list) else ""
            # WorkBuddy embeds a very large system context ahead of the real user request.
            if event["role"] == "user":
                match = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text, flags=re.DOTALL | re.IGNORECASE)
                text = match.group(1) if match else text
            if not text.strip():
                continue
            conversation_id = str(event.get("sessionId") or path.stem)
            session = sessions.get(conversation_id, {})
            workspace = event.get("cwd") or session.get("cwd")
            timestamp = event.get("timestamp") or session.get("updated_at") or path.stat().st_mtime
            if isinstance(timestamp, (int, float)) and timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            title = session.get("custom_title") or session.get("title") or f"WorkBuddy {event['role']} message"
            records.append({
                "kind": "message", "title": str(title)[:240], "body": text[:80_000], "locator": str(path),
                "conversation_id": conversation_id, "workspace_path": workspace,
                "project_title": Path(workspace).name if workspace else path.parent.name, "updated_at": timestamp,
                "sequence": sequence, "metadata": {"role": event["role"], "source_format": "workbuddy_jsonl"},
            })
    return records


def qoder_records() -> list[dict]:
    root = Path(os.environ.get("APPDATA", "")) / "Qoder" / "User" / "workspaceStorage"
    records: list[dict] = []
    if not root.exists():
        return records
    for db_path in root.glob("*/state.vscdb"):
        snapshot = None
        try:
            snapshot = _snapshot_sqlite(db_path)
            with _snapshot_connection(snapshot) as conn:
                rows = conn.execute("SELECT key FROM ItemTable WHERE key LIKE 'aicoding-chat-%.state'").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            if snapshot is not None:
                _cleanup_snapshot(snapshot)
        for (key,) in rows:
            records.append({
                "kind": "workspace_pointer", "title": "Qoder chat workspace (metadata only)",
                "body": "Qoder exposes local chat view state here; verified local storage does not contain transcript text.",
                "locator": str(db_path), "conversation_id": key, "workspace_path": None, "project_title": f"Qoder workspace {db_path.parent.name}",
                "updated_at": db_path.stat().st_mtime, "metadata": {"availability": "metadata_only"},
            })
    return records


def qoderwork_cn_records() -> list[dict]:
    """Read QoderWork CN's authoritative SQLite chat/message store read-only."""
    path = Path(os.environ.get("APPDATA", "")) / "QoderWork CN" / "data" / "agents.db"
    if not path.exists():
        return []
    snapshot = _snapshot_sqlite(path)
    try:
        with _snapshot_connection(snapshot) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT m.id,m.chat_id,m.sub_chat_id,m.sequence,m.role,m.searchable_text,m.created_at,m.updated_at,
                          c.name,c.worktree_path,c.output_directory,c.branch,c.project_id
                   FROM messages m JOIN chats c ON c.id=m.chat_id
                   WHERE c.deleted_at IS NULL AND m.searchable_text IS NOT NULL AND trim(m.searchable_text) != ''
                   ORDER BY c.updated_at DESC,m.sequence ASC"""
            ).fetchall()
        records = []
        for row in rows:
            timestamp = row["updated_at"] or row["created_at"] or path.stat().st_mtime
            if isinstance(timestamp, (int, float)) and timestamp > 10_000_000_000:
                timestamp /= 1000
            workspace = row["worktree_path"] or row["output_directory"]
            records.append({
                "kind": "message", "title": row["name"] or f"QoderWork CN {row['role']} message",
                "body": row["searchable_text"][:80_000], "locator": str(path), "conversation_id": row["chat_id"],
                "workspace_path": workspace, "project_title": Path(workspace).name if workspace else (row["name"] or "QoderWork CN"),
                "updated_at": timestamp, "sequence": row["sequence"],
                "metadata": {"role": row["role"], "sub_chat_id": row["sub_chat_id"], "branch": row["branch"], "project_id": row["project_id"], "source_format": "sqlite_snapshot"},
            })
        return records
    finally:
        _cleanup_snapshot(snapshot)


def _is_text_candidate(path: Path, max_file_bytes: int, include_sensitive: bool = False) -> bool:
    parts = {part.lower() for part in path.parts}
    allowed_sensitive_name = include_sensitive and path.name.lower() in EXCLUDED_NAMES
    return (path.suffix.lower() in TEXT_EXTENSIONS or allowed_sensitive_name) and (include_sensitive or path.name.lower() not in EXCLUDED_NAMES) and not (parts & FILESYSTEM_EXCLUDED_PARTS) and path.stat().st_size <= max_file_bytes


def _read_text(path: Path) -> str | None:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeError, OSError):
            continue
    return None


def filesystem_records(config: Config, extra_roots: Iterable[str] = (), *, scan_status: dict | None = None, stop_event=None) -> list[dict]:
    # Configured roots are authoritative and must be scanned first. A client
    # may report the workspace parent; accepting that broad ancestor first can
    # exhaust the character budget before the configured project is reached.
    roots: list[Path] = []
    failures: list[str] = []
    configured_resolved: list[Path] = []
    profile = HOME.resolve()
    def too_broad(path: Path) -> bool:
        return path == Path(path.anchor) or path == profile or path in profile.parents

    for value in (config.project_roots or []):
        candidate = Path(str(value).removeprefix("\\\\?\\"))
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if too_broad(resolved):
            raise ValueError(f"Refusing broad filesystem root: {candidate}; choose a specific project directory")
        if candidate.exists() and candidate.is_dir() and resolved not in configured_resolved:
            roots.append(candidate)
            configured_resolved.append(resolved)
        elif not candidate.is_dir():
            failures.append(f"Unavailable root: {candidate}")
    workspace_root = HOME / "Desktop" / "codex space"
    for value in dict.fromkeys(extra_roots):
        if not value:
            continue
        candidate = Path(str(value).removeprefix("\\\\?\\"))
        try:
            resolved = candidate.resolve()
            workspace_resolved = workspace_root.resolve()
            # A client may report C:\\Users\\name as its cwd. Never turn that into a full-profile crawl.
            inside_workspace = resolved.is_relative_to(workspace_resolved)
        except (OSError, ValueError):
            inside_workspace = False
            resolved = candidate
            workspace_resolved = workspace_root
        if too_broad(resolved) or not candidate.exists() or not candidate.is_dir() or not (inside_workspace or (candidate / ".git").exists()):
            continue
        # Do not add a parent or nested duplicate of a configured root. In
        # particular, never turn the whole workspace parent into an implicit root.
        if resolved == workspace_resolved or any(
            resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved)
            for root in configured_resolved
        ):
            continue
        if resolved not in configured_resolved and all(resolved != root.resolve() for root in roots):
            roots.append(candidate)
    # Collect paths first, not bodies: directory traversal order must not let
    # an early project consume the entire budget before later handoffs.
    candidates: list[tuple[int, float, str, Path, Path]] = []
    seen_paths: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for directory, child_dirs, filenames in os.walk(root, topdown=True, onerror=lambda error: failures.append(str(error))):
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Filesystem sync cancelled")
            child_dirs[:] = [name for name in child_dirs if name.lower() not in FILESYSTEM_EXCLUDED_PARTS and name not in {
                "2026-06-08_chat-rag-index", "2026-06-08_gemini聊天批量导出", "2026-06-08_yuanbao聊天批量导出", "migration-2026-09-02"}]
            for filename in filenames:
                path = Path(directory) / filename
                try:
                    if filename.lower() in LOCKFILE_NAMES or not _is_text_candidate(path, config.max_file_bytes, config.full_memory_mode) or not path.is_file():
                        continue
                    key = os.path.normcase(os.path.abspath(path))
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    name = filename.lower()
                    important = path.suffix.lower() in {".md", ".txt"} and any(
                        word in name for word in ("readme", "agents", "handoff", "交接", "说明", "接手")
                    )
                    priority = 0 if important else (1 if path.suffix.lower() == ".md" else 2)
                    candidates.append((priority, -path.stat().st_mtime, str(path).lower(), path, root))
                except (OSError, UnicodeError) as error:
                    failures.append(str(error))
                    continue
    records: list[dict] = []
    indexed_characters = 0
    partial = False
    for _, negative_mtime, _, path, root in sorted(candidates):
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Filesystem sync cancelled")
        if indexed_characters >= MAX_FILESYSTEM_CHARACTERS:
            partial = True
            break
        body = _read_text(path)
        if body is None:
            failures.append(f"Unreadable file: {path}")
        if not body or not body.strip():
            continue
        body = body[:80_000]
        remaining = MAX_FILESYSTEM_CHARACTERS - indexed_characters
        if len(body) > remaining:
            partial = True
        body = body[:remaining]
        indexed_characters += len(body)
        records.append({
            "kind": "file", "title": path.name, "body": body, "locator": str(path),
            "conversation_id": None, "workspace_path": str(root), "project_title": root.name,
            "updated_at": -negative_mtime, "metadata": {"extension": path.suffix, "source_format": "file"},
        })
    if scan_status is not None:
        scan_status.update({
            "state": "partial" if partial or failures else ("ok" if roots else "not_configured"),
            "allow_delete": bool(roots) and not failures,
            "detail": (f"部分收录：达到 {MAX_FILESYSTEM_CHARACTERS:,} 字符上限；" if partial else "")
                      + f"已收录 {len(records):,} 个文件；已排除锁文件，优先说明/交接及近期文档。"
                      + ((" 本次保留未读到的旧索引：" + "; ".join(failures[:3])) if failures else ""),
        })
    return records


def _pi_content_text(content: Any) -> str:
    """Flatten Pi message content to plain text, keeping only visible text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", "")).strip()
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
    return ""


def pi_records() -> list[dict]:
    """Read the open-source Pi coding agent's local JSONL session tree.

    Location resolves in this order: PI_CODING_AGENT_SESSION_DIR, then
    PI_CODING_AGENT_DIR/agent/sessions, then ~/.pi/agent/sessions. Each session
    is one append-only JSONL file with a tree of typed entries (id/parentId).
    """
    session_dir = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if session_dir:
        root = Path(session_dir)
    else:
        base = os.environ.get("PI_CODING_AGENT_DIR")
        root = Path(base) / "agent" / "sessions" if base else HOME / ".pi" / "agent" / "sessions"
    records: list[dict] = []
    if not root.exists():
        return records
    header: dict[Path, dict] = {}
    for path in root.rglob("*.jsonl"):
        for sequence, event in _safe_json_lines(path):
            etype = event.get("type")
            if etype == "session":
                header[path] = {"cwd": event.get("cwd"), "id": event.get("id")}
                continue
            role = None
            content = None
            if etype == "message":
                msg = event.get("message") or {}
                role = msg.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = msg.get("content")
            elif etype == "bashExecution":
                role = "bash"
                content = [
                    {"type": "text", "text": "$ " + (event.get("command") or "")},
                    {"type": "text", "text": event.get("output") or ""},
                ]
            elif etype == "toolResult":
                role = "tool"
                content = event.get("content")
            else:
                continue
            text = _pi_content_text(content)
            if not text.strip():
                continue
            h = header.get(path, {})
            cwd = event.get("cwd") or h.get("cwd")
            timestamp = event.get("timestamp") or path.stat().st_mtime
            from .web_import import timestamp as parse_timestamp
            timestamp = parse_timestamp(timestamp) or path.stat().st_mtime
            if isinstance(timestamp, (int, float)) and timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            conversation_id = h.get("id") or event.get("id") or path.stem
            records.append({
                "kind": "message", "title": f"Pi {role} message"[:240], "body": text[:80_000],
                "locator": str(path), "conversation_id": str(conversation_id),
                "workspace_path": cwd, "project_title": Path(cwd).name if cwd else path.parent.name,
                "updated_at": timestamp, "sequence": sequence,
                "metadata": {"role": role, "source_format": "pi_jsonl"},
            })
    return records


def dsh_records() -> list[dict]:
    """Read DeepSeek Harness (dsh) local session logs.

    Sessions live under DSH_HOME/sessions (DSH_HOME defaults to ~/.dsh), one
    append-only JSONL event log per session. Recent releases compress the log
    with zstd (session.jsonl.zstd); plain *.jsonl is also supported. Text is
    extracted generically because the event schema is not documented.
    """
    home = os.environ.get("DSH_HOME")
    root = Path(home) / "sessions" if home else HOME / ".dsh" / "sessions"
    records: list[dict] = []
    if not root.exists():
        return records
    try:
        import io
        import zstandard
        dctx = zstandard.ZstdDecompressor()
    except Exception:
        dctx = None
    for path in list(root.rglob("*.jsonl.zstd")) + list(root.rglob("*.jsonl")):
        is_zstd = path.name.endswith(".zstd") or path.suffix == ".zstd"
        if is_zstd:
            if dctx is None:
                continue
            try:
                with dctx.stream_reader(io.BytesIO(path.read_bytes())) as reader:
                    text = reader.read().decode("utf-8", "replace")
            except Exception:
                continue
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError):
                continue
        for sequence, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            body = "\n".join(_strings(event))
            if not body.strip():
                continue
            cwd = event.get("cwd") or event.get("workspace") or event.get("workspace_path")
            timestamp = event.get("timestamp") or event.get("ts") or event.get("createdAt") or event.get("updatedAt") or path.stat().st_mtime
            if isinstance(timestamp, (int, float)) and timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            title = str(event.get("type") or event.get("kind") or event.get("role") or path.parent.name)[:240]
            conversation_id = str(event.get("sessionId") or event.get("session_id") or event.get("id") or path.parent.name)
            records.append({
                "kind": "message", "title": title, "body": body[:80_000], "locator": str(path),
                "conversation_id": conversation_id, "workspace_path": cwd,
                "project_title": Path(cwd).name if cwd else path.parent.name,
                "updated_at": timestamp, "sequence": sequence,
                "metadata": {"source_format": "dsh_jsonl"},
            })
    return records


def _zcode_task_index(db_path: Path) -> dict[str, dict[str, Any]]:
    """Read ZCode's small task index without opening its live database for writes."""
    if not db_path.exists():
        return {}
    snapshot: Path | None = None
    try:
        snapshot = _snapshot_sqlite(db_path)
        with _snapshot_connection(snapshot) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT task_id,workspace_path,title,provider,model,updated_at,deleted
                   FROM tasks WHERE deleted=0"""
            ).fetchall()
        return {str(row["task_id"]): dict(row) for row in rows}
    except (OSError, sqlite3.Error) as error:
        LOG.warning("Could not read ZCode task index: %s", error)
        return {}
    finally:
        if snapshot is not None:
            _cleanup_snapshot(snapshot)


def _zcode_visible_text(value: Any) -> str:
    """Extract visible text while ignoring tool arguments and binary blocks."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in {"text", "output_text"}:
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts)
    return ""


def zcode_records() -> list[dict]:
    """Read ZCode's local model-rollout JSONL files as chat messages.

    ZCode writes the full request context on every model call. We keep only
    the last user message for each turn and visible main-turn response text,
    which removes repeated context and avoids importing system prompts,
    tool arguments, response headers, or credentials.
    """
    root = HOME / ".zcode"
    rollout_root = root / "cli" / "rollout"
    if not rollout_root.exists():
        return []
    task_index = _zcode_task_index(root / "v2" / "tasks-index.sqlite")
    from .web_import import timestamp as parse_timestamp

    records: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for path in sorted(rollout_root.glob("model-io-sess_*.jsonl")):
        fallback_session_id = path.stem.removeprefix("model-io-")
        for sequence, event in _safe_json_lines(path):
            session_id = str(event.get("sessionId") or fallback_session_id)
            if not session_id or session_id == "no-session":
                continue
            task = task_index.get(session_id, {})
            title = str(task.get("title") or f"ZCode {session_id}")[:240]
            workspace = task.get("workspace_path")
            project_title = Path(workspace).name if workspace else "ZCode"
            event_time = parse_timestamp(event.get("startedAt")) or parse_timestamp(event.get("completedAt"))
            task_time = parse_timestamp(task.get("updated_at"))
            updated_at = task_time or event_time or path.stat().st_mtime
            turn_id = str(event.get("turnId") or sequence)
            messages = event.get("request", {}).get("messages", [])
            last_user_text = ""
            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, dict) or message.get("role") != "user":
                        continue
                    text = _zcode_visible_text(message.get("content"))
                    if text:
                        last_user_text = text
            if last_user_text:
                key = (session_id, turn_id, "user:" + hashlib.sha256(last_user_text.encode("utf-8", "replace")).hexdigest())
                if key not in seen:
                    seen.add(key)
                    records.append({
                        "kind": "message", "title": title, "body": last_user_text[:80_000],
                        "locator": str(path), "conversation_id": session_id,
                        "workspace_path": workspace, "project_title": project_title,
                        "updated_at": updated_at, "sequence": sequence * 2,
                        "metadata": {"role": "user", "provider": task.get("provider"),
                                     "model": task.get("model"), "source_format": "zcode_rollout_jsonl"},
                    })
            response = event.get("response", {})
            response_text = _zcode_visible_text(response.get("text")) if isinstance(response, dict) else ""
            if response_text and event.get("querySource") in {None, "main_turn"}:
                key = (session_id, turn_id, "assistant:" + hashlib.sha256(response_text.encode("utf-8", "replace")).hexdigest())
                if key not in seen:
                    seen.add(key)
                    records.append({
                        "kind": "message", "title": title, "body": response_text[:80_000],
                        "locator": str(path), "conversation_id": session_id,
                        "workspace_path": workspace, "project_title": project_title,
                        "updated_at": updated_at, "sequence": sequence * 2 + 1,
                        "metadata": {"role": "assistant", "provider": task.get("provider"),
                                     "model": task.get("model"), "source_format": "zcode_rollout_jsonl"},
                    })
    return records
