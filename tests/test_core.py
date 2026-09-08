import tempfile
import unittest
import sqlite3
import os
import threading
import time
from pathlib import Path

from memory_hub import db
from memory_hub import ai
from memory_hub import sources
from memory_hub import config as config_module
from memory_hub import indexer
from memory_hub.config import Config


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / "test.sqlite"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.previous
        self.temp.cleanup()

    def test_fts_search_and_source_replacement(self):
        db.replace_source("fixture", [{"title": "Hermes repair", "body": "The 401 token fix succeeded", "locator": "C:/fixture.md", "workspace_path": "C:/project"}])
        self.assertEqual(1, len(db.search("401")))
        db.replace_source("fixture", [{"title": "New", "body": "new record", "locator": "C:/new.md"}])
        self.assertEqual([], db.search("401"))

    def test_invalid_fts_query_falls_back(self):
        db.replace_source("fixture", [{"title": "A", "body": "hello world", "locator": "C:/a"}])
        self.assertIsInstance(db.search('"unterminated'), list)

    def test_embedding_sync_is_incremental_and_redacts_outbound_text(self):
        db.replace_source("fixture", [{"title": "Key", "body": "api_key=sk-abcdefghijklmnopqr", "locator": "C:/key"}])
        config = Config(embedding={"enabled": True, "base_url": "http://fixture/v1", "model": "embed"})
        original = ai._post
        calls = []
        try:
            def fake_post(section, path, payload):
                calls.append(payload)
                return {"data": [{"index": index, "embedding": [1.0, 0.0]} for index in range(len(payload["input"]))]}
            ai._post = fake_post
            self.assertEqual(1, ai.sync_embeddings(config)["embedded"])
            self.assertNotIn("sk-abcdefghijklmnopqr", calls[0]["input"][0])
            self.assertEqual(0, ai.sync_embeddings(config)["embedded"])
        finally:
            ai._post = original

    def test_full_memory_mode_keeps_outbound_text(self):
        self.assertEqual("api_key=sk-abcdefghijklmnopqr", ai.outbound_text(Config(full_memory_mode=True), "api_key=sk-abcdefghijklmnopqr"))

    def test_sqlite_snapshot_releases_handles_and_cleans_repeatedly(self):
        source_dir = Path(self.temp.name) / "source"
        source_dir.mkdir()
        source = source_dir / "state.db"
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE values_table(value TEXT)")
            conn.execute("INSERT INTO values_table VALUES ('fixture')")
        snapshot_root = Path(self.temp.name) / "snapshots"
        previous_root = sources.SNAPSHOT_ROOT
        sources.SNAPSHOT_ROOT = snapshot_root
        try:
            for _ in range(3):
                snapshot = sources._snapshot_sqlite(source)
                with sources._snapshot_connection(snapshot) as conn:
                    self.assertEqual("fixture", conn.execute("SELECT value FROM values_table").fetchone()[0])
                sources._cleanup_snapshot(snapshot)
                self.assertFalse(snapshot.parent.exists())
        finally:
            sources.SNAPSHOT_ROOT = previous_root

    def test_qoder_snapshot_cleanup_does_not_reuse_previous_path(self):
        appdata = Path(self.temp.name) / "appdata"
        workspace = appdata / "Qoder" / "User" / "workspaceStorage"
        for name in ("one", "two"):
            folder = workspace / name
            folder.mkdir(parents=True)
            conn = sqlite3.connect(folder / "state.vscdb")
            try:
                conn.execute("CREATE TABLE ItemTable(key TEXT PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO ItemTable VALUES ('aicoding-chat-fixture.state', '{}')")
                conn.commit()
            finally:
                conn.close()
        snapshot_root = Path(self.temp.name) / "snapshots"
        previous_root = sources.SNAPSHOT_ROOT
        previous_appdata = os.environ.get("APPDATA")
        sources.SNAPSHOT_ROOT = snapshot_root
        os.environ["APPDATA"] = str(appdata)
        try:
            records = sources.qoder_records()
            self.assertEqual(2, len(records))
            self.assertFalse(snapshot_root.exists() and any(snapshot_root.iterdir()))
        finally:
            sources.SNAPSHOT_ROOT = previous_root
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    def test_filesystem_prioritizes_configured_root_over_workspace_parent(self):
        fake_home = Path(self.temp.name) / "home"
        workspace = fake_home / "Desktop" / "codex space"
        configured_root = workspace / "按对话归类"
        configured_root.mkdir(parents=True)
        (workspace / "parent.md").write_text("parent should not consume the scan budget", encoding="utf-8")
        (configured_root / "project.md").write_text("configured project content", encoding="utf-8")
        previous_home = sources.HOME
        sources.HOME = fake_home
        try:
            records = sources.filesystem_records(
                Config(project_roots=[str(configured_root)]), [str(workspace), "\\\\?\\" + str(workspace)]
            )
            self.assertEqual(["project.md"], [record["title"] for record in records])
        finally:
            sources.HOME = previous_home

    def test_config_repairs_literal_question_mark_workspace_path(self):
        app_data = Path(self.temp.name) / "memory-hub-config"
        workspace = Path(self.temp.name) / "按对话归类"
        workspace.mkdir()
        previous_app_data = config_module.APP_DATA
        previous_workspace = config_module.WORKSPACE
        config_module.APP_DATA = app_data
        config_module.WORKSPACE = workspace
        try:
            app_data.mkdir()
            (app_data / "config.json").write_text(
                '{"project_roots":["C:\\\\Users\\\\fixture-user\\\\Desktop\\\\codex space\\\\?????"]}',
                encoding="utf-8",
            )
            loaded = config_module.load_config()
            self.assertEqual([str(workspace)], loaded.project_roots)
            self.assertIn("按对话归类", (app_data / "config.json").read_text(encoding="utf-8"))
        finally:
            config_module.APP_DATA = previous_app_data
            config_module.WORKSPACE = previous_workspace

    def test_filesystem_budget_keeps_handoffs_and_reports_partial(self):
        root = Path(self.temp.name) / "projects"
        old = root / "a-old"
        new = root / "z-new"
        old.mkdir(parents=True)
        new.mkdir()
        (old / "package-lock.json").write_text("lock" * 100, encoding="utf-8")
        (old / "data.json").write_text("x" * 100, encoding="utf-8")
        (new / "README.md").write_text("project introduction", encoding="utf-8")
        (new / "HANDOFF.md").write_text("continue here", encoding="utf-8")
        previous_budget = sources.MAX_FILESYSTEM_CHARACTERS
        try:
            sources.MAX_FILESYSTEM_CHARACTERS = 50
            scan_status = {}
            records = sources.filesystem_records(Config(project_roots=[str(root)]), scan_status=scan_status)
            self.assertEqual({"README.md", "HANDOFF.md"}, {r["title"] for r in records[:2]})
            self.assertNotIn("package-lock.json", [r["title"] for r in records])
            self.assertEqual(50, sum(len(r["body"]) for r in records))
            db.replace_source("filesystem", records, **scan_status)
            self.assertEqual("partial", db.status()[0]["state"])
            self.assertIn("部分收录", db.status()[0]["detail"])
        finally:
            sources.MAX_FILESYSTEM_CHARACTERS = previous_budget

    def test_sync_prunes_status_rows_for_removed_adapters(self):
        db.replace_source("obsolete_adapter", [{"title": "old", "body": "old", "locator": "C:/old"}])
        previous_sources = indexer.SOURCES
        previous_filesystem = sources.filesystem_records
        indexer.SOURCES = {"fixture": lambda: []}
        sources.filesystem_records = lambda config, roots, **kwargs: []
        try:
            indexer.sync(Config(project_roots=[]))
            source_names = {row["source"] for row in db.status()}
            self.assertNotIn("obsolete_adapter", source_names)
            self.assertEqual({"fixture", "filesystem"}, source_names)
        finally:
            indexer.SOURCES = previous_sources
            sources.filesystem_records = previous_filesystem

    def test_background_sync_rejects_overlapping_run(self):
        entered = threading.Event()
        release = threading.Event()
        previous_sources = indexer.SOURCES
        previous_filesystem = sources.filesystem_records

        def blocking_reader():
            entered.set()
            release.wait(2)
            return []

        indexer.SOURCES = {"fixture": blocking_reader}
        sources.filesystem_records = lambda config, roots, **kwargs: []
        try:
            self.assertEqual("started", indexer.start_sync(Config(project_roots=[]))["state"])
            self.assertTrue(entered.wait(1))
            self.assertEqual("already_running", indexer.start_sync(Config(project_roots=[]))["state"])
            release.set()
            deadline = time.time() + 2
            while indexer.sync_status()["running"] and time.time() < deadline:
                time.sleep(0.01)
            self.assertFalse(indexer.sync_status()["running"])
        finally:
            release.set()
            indexer.SOURCES = previous_sources
            sources.filesystem_records = previous_filesystem

    def test_source_fingerprint_skips_unchanged_adapter(self):
        calls = []
        previous_sources = indexer.SOURCES
        previous_fingerprints = dict(indexer._SOURCE_FINGERPRINTS)
        previous_fingerprint = sources.source_fingerprint
        previous_filesystem = sources.filesystem_records

        def reader():
            calls.append(True)
            return [{"title": "stable", "body": "same", "locator": "C:/stable"}]

        indexer.SOURCES = {"fixture": reader}
        sources.source_fingerprint = lambda name: "fixed-v1"
        sources.filesystem_records = lambda config, roots, **kwargs: []
        indexer._SOURCE_FINGERPRINTS.clear()
        try:
            indexer.sync(Config(project_roots=[]))
            indexer.sync(Config(project_roots=[]))
            self.assertEqual(1, len(calls))
            self.assertEqual(1, db.source_count("fixture"))
        finally:
            indexer.SOURCES = previous_sources
            indexer._SOURCE_FINGERPRINTS.clear()
            indexer._SOURCE_FINGERPRINTS.update(previous_fingerprints)
            sources.source_fingerprint = previous_fingerprint
            sources.filesystem_records = previous_filesystem

    def test_codex_cache_ignores_metadata_only_timestamp_change(self):
        fake_home = Path(self.temp.name) / "codex-home"
        codex_root = fake_home / ".codex"
        codex_root.mkdir(parents=True)
        rollout = codex_root / "rollout.jsonl"
        rollout.write_text('{"type":"message","text":"stable body"}\n', encoding="utf-8")
        state = codex_root / "state_5.sqlite"
        conn = sqlite3.connect(state)
        try:
            conn.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, cwd TEXT, title TEXT, updated_at REAL, archived INTEGER)")
            conn.execute("INSERT INTO threads VALUES ('thread-1', ?, 'C:/project', 'Title', 1, 0)", (str(rollout),))
            conn.commit()
        finally:
            conn.close()
        previous_home = sources.HOME
        previous_cache = dict(sources._CODEX_RECORD_CACHE)
        previous_reader = sources._safe_json_lines
        calls = []

        def counting_reader(path):
            calls.append(path)
            yield from previous_reader(path)

        sources.HOME = fake_home
        sources._CODEX_RECORD_CACHE.clear()
        sources._safe_json_lines = counting_reader
        try:
            first = sources.codex_records()
            conn = sqlite3.connect(state)
            try:
                conn.execute("UPDATE threads SET updated_at=2")
                conn.commit()
            finally:
                conn.close()
            second = sources.codex_records()
            self.assertEqual(1, len(calls))
            self.assertEqual(1, len(first))
            self.assertEqual(2, second[0]["updated_at"])
        finally:
            sources.HOME = previous_home
            sources._CODEX_RECORD_CACHE.clear()
            sources._CODEX_RECORD_CACHE.update(previous_cache)
            sources._safe_json_lines = previous_reader


if __name__ == "__main__":
    unittest.main()
