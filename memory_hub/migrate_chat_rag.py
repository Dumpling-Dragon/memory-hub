"""One-time, restartable consolidation. Dry-run by default; never writes the old DB."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from . import db
from .config import WORKSPACE
from .web_import import LEGACY_MODEL, web_records

SOURCE_MAP = {"codex": "codex", "claude": "claude_code", "hermes": "hermes"}

def normalized(text):
    return re.sub(r"\s+", "", text)

def parts(text):
    text = re.sub(r"\A平台：[^\n]*\n标题：[^\n]*\n归属：[^\n]*\n(?:工作区：[^\n]*\n)?\n", "", text)
    # Old chunks concatenate assistant turns; native rows retain each turn separately.
    # Check every complete/partial text line in the same conversation, not a fabricated
    # concatenation that fails whenever the native log also contains an intervening event.
    return [normalized(line) for line in text.splitlines() if normalized(line) and line.strip() not in {"用户：", "AI："}]

def record_id(source, record):
    return db.stable_id(source, record["locator"], record.get("conversation_id", ""), record.get("sequence", ""))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-db", type=Path, default=WORKSPACE / "2026-06-08_chat-rag-index/data/chat_index.sqlite")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    old = sqlite3.connect(args.old_db.resolve().as_uri() + "?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    legacy = [dict(row) for row in old.execute("SELECT * FROM chunks")]
    old.close()
    native = collections.defaultdict(list)
    with sqlite3.connect(db.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        for source, cid, body in conn.execute("SELECT source,conversation_id,body FROM documents WHERE source IN ('codex','claude_code','hermes')"):
            native[(source, cid)].append(normalized(body))
    native_text = {key: "\n".join(bodies) for key, bodies in native.items()}
    records = {source: web_records(source) for source in ("gemini", "yuanbao")}
    records.update({source + "_archive": [] for source in SOURCE_MAP.values()})
    vector_lookup = {}
    for row in legacy:
        key = (row["platform"], row["conversation_id"], row["group_index"], row["text"])
        vector_lookup[key] = row
    report = {"old_chunks": len(legacy), "applied": args.apply, "native_covered": {}, "sources": {}}
    covered, seen = collections.Counter(), set()
    for row in legacy:
        if row["platform"] not in SOURCE_MAP:
            continue
        source = SOURCE_MAP[row["platform"]]
        cid = row["conversation_id"]
        # Require actual content coverage, never merely a matching title or session ID.
        fragments = parts(row["text"])
        body = native_text.get((source, cid), "")
        if fragments and all(fragment in body for fragment in fragments):
            covered[row["platform"]] += 1
            continue
        key = (source, cid, normalized(row["text"]))
        if key in seen:
            covered[row["platform"]] += 1
            continue
        seen.add(key)
        path = Path(row["source_json"])
        record = {"kind": "legacy_chat_chunk", "title": row["title"], "body": row["text"],
            "locator": row["source_json"], "conversation_id": cid, "sequence": row["chunk_id"],
            "updated_at": path.stat().st_mtime if path.exists() else None,
            "metadata": {"source_format": "chat_rag_archive", "owner": row["owner"], "url": row["url"],
                "legacy_chunk_id": row["chunk_id"], "group_index": row["group_index"],
                "embedding_model": row["embedding_model"], "time_basis": "source_file_mtime",
                "coverage": "partial_native" if body else "legacy_only"}}
        records.setdefault(source + "_archive", []).append(record)
    report["native_covered"] = dict(covered)
    # Refuse to silently discard a web conversation missing from current exports.
    for platform in ("gemini", "yuanbao"):
        current = {r["conversation_id"] for r in records[platform]}
        missing = {r["conversation_id"] for r in legacy if r["platform"] == platform} - current
        if missing:
            raise RuntimeError(f"{platform}: {len(missing)} legacy conversations missing from exports; preserve those exports before migration")
    digest = hashlib.sha256()
    for source, incoming in records.items():
        platform = {"codex_archive": "codex", "claude_code_archive": "claude", "hermes_archive": "hermes"}.get(source, source)
        pairs = []
        for record in incoming:
            key = (platform, record["conversation_id"], record["metadata"]["group_index"], record["body"])
            previous = vector_lookup.get(key)
            if previous is not None:
                pairs.append((record, previous))
                digest.update(previous["embedding"])
        report["sources"][source] = {"documents": len(incoming), "reusable_vectors": len(pairs), "needs_embedding": len(incoming)-len(pairs)}
        if not args.apply:
            continue
        db.replace_source(source, incoming, "web export" if source in ("gemini", "yuanbao") else "legacy supplement")
        with db.connect() as conn:
            for record, previous in pairs:
                doc_id = record_id(source, record)
                current = conn.execute("SELECT content_hash FROM documents WHERE id=?", (doc_id,)).fetchone()
                conn.execute("""INSERT INTO embeddings(document_id,content_hash,model,dimensions,vector,indexed_at)
                    VALUES(?,?,?,?,?,strftime('%s','now')) ON CONFLICT(document_id,model) DO UPDATE SET
                    content_hash=excluded.content_hash,dimensions=excluded.dimensions,vector=excluded.vector""",
                    (doc_id, current[0], previous["embedding_model"], previous["embedding_dim"], previous["embedding"]))
        # Verify copied bytes as well as row counts before declaring vector reuse successful.
        with db.connect() as conn:
            for record, previous in pairs:
                actual = conn.execute("SELECT vector FROM embeddings WHERE document_id=? AND model=?", (record_id(source,record), previous["embedding_model"])).fetchone()
                if actual is None or actual[0] != previous["embedding"]:
                    raise RuntimeError("Imported vector byte verification failed")
    report["reused_vector_sha256"] = digest.hexdigest()
    if args.apply:
        report["status"] = db.embedding_status("BAAI/bge-m3")
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
