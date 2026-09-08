from __future__ import annotations

import hashlib
import json
import ntpath
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import APP_DATA


DB_PATH = APP_DATA / "memory_hub.sqlite"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                workspace_path TEXT UNIQUE,
                title TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                project_id TEXT REFERENCES projects(id),
                conversation_id TEXT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                locator TEXT NOT NULL,
                source_updated_at REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                content_hash TEXT NOT NULL,
                indexed_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);
            CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
            CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(source_updated_at DESC);
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TABLE IF NOT EXISTS source_status (
                source TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                detail TEXT NOT NULL,
                document_count INTEGER NOT NULL DEFAULT 0,
                indexed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                indexed_at REAL NOT NULL,
                PRIMARY KEY(document_id, model)
            );
            CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);
            """
        )


def stable_id(*parts: object) -> str:
    return hashlib.sha256("\0".join(str(part) for part in parts).encode("utf-8", "replace")).hexdigest()


def project_path(workspace_path: str) -> tuple[str, str]:
    raw = str(workspace_path)
    windows_path = bool(ntpath.splitdrive(raw)[0]) or raw.startswith("\\\\")
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    normalized = ntpath.normpath(raw.removeprefix("\\\\?\\")) if windows_path else str(Path(raw))
    key = normalized.lower() if windows_path else normalized
    return normalized, key


def upsert_project(conn: sqlite3.Connection, workspace_path: str | None, title: str, projects=None) -> str | None:
    if not workspace_path:
        return None
    normalized, key = project_path(workspace_path)
    if projects is None:
        projects = {project_path(row["workspace_path"])[1]: (row["id"], row["title"]) for row in conn.execute("SELECT * FROM projects")}
    if key in projects:
        project_id, old_title = projects[key]
        if title != old_title:
            conn.execute("UPDATE projects SET title=?,updated_at=? WHERE id=?", (title, time.time(), project_id))
            projects[key] = (project_id, title)
        return project_id
    project_id = stable_id("project", key)
    conn.execute(
        """INSERT INTO projects(id, workspace_path, title, updated_at) VALUES(?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at""",
        (project_id, normalized, title or Path(normalized).name, time.time()),
    )
    projects[key] = (project_id, title)
    return project_id


def replace_source(source: str, records: list[dict], detail: str = "ok", *, state: str = "ok", allow_delete: bool = True) -> int:
    now = time.time()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = {row["id"]: row["content_hash"] for row in conn.execute("SELECT id,content_hash FROM documents WHERE source=?", (source,))}
        projects = {project_path(row["workspace_path"])[1]: (row["id"], row["title"]) for row in conn.execute("SELECT * FROM projects")}
        seen: set[str] = set()
        for record in records:
            record_id = stable_id(source, record["locator"], record.get("conversation_id", ""), record.get("sequence", ""))
            body = record.get("body", "").strip()
            title = record.get("title", "Untitled record").strip()
            content_hash = stable_id(title, body, record.get("metadata", {}))
            if record_id in seen:
                continue
            seen.add(record_id)
            project_id = upsert_project(conn, record.get("workspace_path"), record.get("project_title") or "Untitled project", projects)
            if existing.get(record_id) == content_hash:
                conn.execute("UPDATE documents SET source_updated_at=?,project_id=? WHERE id=?", (record.get("updated_at"), project_id, record_id))
                continue
            if record_id in existing:
                conn.execute("DELETE FROM documents_fts WHERE id=?", (record_id,))
                conn.execute("DELETE FROM embeddings WHERE document_id=?", (record_id,))
                conn.execute("DELETE FROM documents WHERE id=?", (record_id,))
            conn.execute(
                """INSERT INTO documents(id,source,kind,project_id,conversation_id,title,body,locator,source_updated_at,metadata_json,content_hash,indexed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, source, record.get("kind", "message"), project_id, record.get("conversation_id"), title,
                 body, record["locator"], record.get("updated_at"), json.dumps(record.get("metadata", {}), ensure_ascii=False), content_hash, now),
            )
            conn.execute("INSERT INTO documents_fts(id,title,body) VALUES(?,?,?)", (record_id, title, body))
        removed = (set(existing) - seen) if allow_delete else set()
        if removed:
            conn.executemany("DELETE FROM documents_fts WHERE id=?", ((record_id,) for record_id in removed))
            conn.executemany("DELETE FROM embeddings WHERE document_id=?", ((record_id,) for record_id in removed))
            conn.executemany("DELETE FROM documents WHERE id=?", ((record_id,) for record_id in removed))
        count = len((set(existing) - removed) | seen)
        conn.execute(
            """INSERT INTO source_status(source,state,detail,document_count,indexed_at) VALUES(?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET state=excluded.state,detail=excluded.detail,document_count=excluded.document_count,indexed_at=excluded.indexed_at""",
            (source, state, detail, count, now),
        )
    return count


def source_error(source: str, error: Exception | str, *, state: str = "error") -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO source_status(source,state,detail,document_count,indexed_at) VALUES(?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET state=excluded.state,detail=excluded.detail,indexed_at=excluded.indexed_at""",
            (source, state, str(error)[:600], 0, time.time()),
        )


def search(query: str, source: str | None = None, project: str | None = None, limit: int = 30, owner: str | None = None) -> list[dict]:
    query = query.strip()
    limit = max(1, min(limit, 100))
    clauses, filters = [], []
    if source:
        source = {"claude": "claude_code"}.get(source, source)
        clauses.append("(d.source=? OR d.source=?)")
        filters.extend([source, source + "_archive"])
    if project:
        clauses.append("(p.title LIKE ? OR p.workspace_path LIKE ?)")
        filters.extend([f"%{project}%", f"%{project}%"])
    if owner:
        clauses.append("COALESCE(json_extract(d.metadata_json,'$.owner'),'我')=?")
        filters.append(owner)
    extra = (" AND " + " AND ".join(clauses)) if clauses else ""
    base = """SELECT d.*, p.title AS project_title, p.workspace_path,
                     substr(d.body,1,360) AS excerpt, 0 AS rank
              FROM documents d LEFT JOIN projects p ON p.id=d.project_id """
    with connect() as conn:
        if not query:
            return [dict(row) for row in conn.execute(
                base + "WHERE 1=1" + extra +
                " ORDER BY COALESCE(d.source_updated_at,d.indexed_at) DESC LIMIT ?", [*filters, limit])]
        rows = []
        try:
            rows = [dict(row) for row in conn.execute(
                """SELECT d.*,p.title AS project_title,p.workspace_path,
                          snippet(documents_fts,2,'<mark>','</mark>','…',18) AS excerpt,
                          bm25(documents_fts) AS rank
                   FROM documents_fts JOIN documents d ON d.id=documents_fts.id
                   LEFT JOIN projects p ON p.id=d.project_id
                   WHERE documents_fts MATCH ?""" + extra +
                " ORDER BY rank,d.source_updated_at DESC LIMIT ?", [query, *filters, limit])]
        except sqlite3.OperationalError:
            pass
        # unicode61 does not split Chinese sentences. Supplement literal matches,
        # preserving the same source/project/owner filters.
        if len(rows) < limit:
            literal = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            ids = {row["id"] for row in rows}
            matches = conn.execute(base +
                "WHERE (d.title LIKE ? ESCAPE '\\' OR d.body LIKE ? ESCAPE '\\')" + extra +
                " ORDER BY d.source_updated_at DESC LIMIT ?", [literal, literal, *filters, limit])
            for row in matches:
                if row["id"] not in ids:
                    rows.append(dict(row))
                    ids.add(row["id"])
                if len(rows) >= limit:
                    break
        return rows


def recent(limit: int = 30) -> list[dict]:
    return search("", limit=limit)


def get_document(document_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("""SELECT d.*,p.title AS project_title,p.workspace_path FROM documents d
                            LEFT JOIN projects p ON p.id=d.project_id WHERE d.id=?""", (document_id,)).fetchone()
    return dict(row) if row else None


def status() -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM source_status ORDER BY source")]


def source_count(source: str) -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM documents WHERE source=?", (source,)).fetchone()[0])


def source_workspace_paths(source: str) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT p.workspace_path FROM documents d
               JOIN projects p ON p.id=d.project_id
               WHERE d.source=? AND p.workspace_path IS NOT NULL""",
            (source,),
        )
        return [str(row[0]) for row in rows]


def embedding_candidates(model: str, limit: int = 100) -> list[dict]:
    """Return changed documents which do not yet have an embedding for this model."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.id,d.title,d.body,d.content_hash FROM documents d
               LEFT JOIN embeddings e ON e.document_id=d.id AND e.model=? AND e.content_hash=d.content_hash
               WHERE e.document_id IS NULL AND NOT EXISTS (
                   SELECT 1 FROM embeddings reused WHERE reused.document_id=d.id
                   AND reused.content_hash=d.content_hash
                   AND reused.model=json_extract(d.metadata_json,'$.embedding_model'))
               ORDER BY d.indexed_at DESC LIMIT ?""",
            (model, max(1, limit)),
        )
        return [dict(row) for row in rows]


def embedding_pending_count(model: str) -> int:
    with connect() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM documents d LEFT JOIN embeddings e
               ON e.document_id=d.id AND e.model=? AND e.content_hash=d.content_hash
               WHERE e.document_id IS NULL AND NOT EXISTS (
                   SELECT 1 FROM embeddings reused WHERE reused.document_id=d.id
                   AND reused.content_hash=d.content_hash
                   AND reused.model=json_extract(d.metadata_json,'$.embedding_model'))""",
            (model,),
        ).fetchone()[0]


def save_embedding(document_id: str, content_hash: str, model: str, vector: bytes, dimensions: int) -> bool:
    # Keep the returned vector in memory while waiting; do not call the model again.
    for attempt in range(3):
        try:
            with connect() as conn:
                written = conn.execute(
                    """INSERT INTO embeddings(document_id,content_hash,model,dimensions,vector,indexed_at)
                       SELECT id,content_hash,?,?,?,? FROM documents WHERE id=? AND content_hash=?
                       ON CONFLICT(document_id,model) DO UPDATE SET content_hash=excluded.content_hash,dimensions=excluded.dimensions,
                       vector=excluded.vector,indexed_at=excluded.indexed_at""",
                    (model, dimensions, vector, time.time(), document_id, content_hash),
                ).rowcount
            return written > 0
        except sqlite3.OperationalError as error:
            if (getattr(error, "sqlite_errorcode", 0) & 255) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or attempt == 2:
                raise
            time.sleep(attempt + 1)
    return False


def models_for_search(default_model: str) -> list[str]:
    with connect() as conn:
        return [row[0] for row in conn.execute("""SELECT DISTINCT e.model FROM embeddings e
            JOIN documents d ON d.id=e.document_id AND d.content_hash=e.content_hash
            WHERE e.model=? OR e.model=json_extract(d.metadata_json,'$.embedding_model') ORDER BY e.model""", (default_model,))]


def vectors_for_model(model: str) -> Iterator[dict]:
    """Stream vectors only; hydrate the small set of winning documents afterwards."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.id,e.vector,e.dimensions
               FROM embeddings e JOIN documents d ON d.id=e.document_id
               WHERE e.model=? AND e.content_hash=d.content_hash""",
            (model,),
        )
        for row in rows:
            yield dict(row)


def embedding_status(model: str | None = None) -> dict:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        if model:
            ready = conn.execute("""SELECT COUNT(DISTINCT e.document_id) FROM embeddings e
                JOIN documents d ON d.id=e.document_id AND d.content_hash=e.content_hash
                WHERE e.model=? OR e.model=json_extract(d.metadata_json,'$.embedding_model')""", (model,)).fetchone()[0]
        else:
            ready = conn.execute("SELECT COUNT(DISTINCT document_id) FROM embeddings").fetchone()[0]
        models = [dict(row) for row in conn.execute("SELECT model,COUNT(*) AS embedded FROM embeddings GROUP BY model")]
    return {"documents": total, "embedded": ready, "pending": total-ready, "model": model, "models": models}
