import io
import json
import struct
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from memory_hub import ai, db, sources, indexer, mcp
from memory_hub.config import Config
from memory_hub.app import create_app


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "fixture.sqlite")
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def record(self, **values):
        return {"title": "fixture", "body": "fixture text", "locator": "C:/fixture.txt", **values}

    def test_case_variants_and_long_prefix_share_project(self):
        records = [self.record(locator=f"C:/{i}", workspace_path=path) for i, path in enumerate([
            "C:/Project", "c:/project", "\\\\?\\C:\\Project", "C:\\Project\\sub\\.."
        ])]
        db.replace_source("fixture", records)
        self.assertEqual(1, len({row["project_id"] for row in db.search("")}))

    def test_existing_legacy_project_id_preserved(self):
        with db.connect() as conn:
            conn.execute("INSERT INTO projects VALUES (?,?,?,?)", ("legacy", "\\\\?\\C:\\Project", "old", 1))
        db.replace_source("fixture", [self.record(workspace_path="c:/project")])
        self.assertEqual("legacy", db.search("")[0]["project_id"])

    def test_workspace_change_preserves_vector(self):
        db.replace_source("fixture", [self.record(workspace_path="C:/Old")])
        row = db.search("")[0]
        db.save_embedding(row["id"], row["content_hash"], "embed", struct.pack("<2f", 1, 0), 2)
        db.replace_source("fixture", [self.record(workspace_path="C:/New")])
        self.assertEqual("C:\\New", db.search("")[0]["workspace_path"])
        with closing(db.vectors_for_model("embed")) as rows:
            self.assertEqual(1, len(list(rows)))

    def test_missing_root_retains_old_documents_and_vectors(self):
        db.replace_source("filesystem", [self.record()])
        row = db.search("")[0]
        db.save_embedding(row["id"], row["content_hash"], "embed", struct.pack("<2f", 1, 0), 2)
        status = {}
        rows = sources.filesystem_records(Config(project_roots=[str(self.root / "missing")]), scan_status=status)
        db.replace_source("filesystem", rows, **status)
        self.assertEqual("partial", db.status()[0]["state"])
        self.assertEqual(1, db.source_count("filesystem"))
        self.assertEqual(1, db.embedding_status("embed")["embedded"])

    def test_empty_readable_root_removes_deleted_files(self):
        db.replace_source("filesystem", [self.record()])
        empty = self.root / "empty"
        empty.mkdir()
        status = {}
        rows = sources.filesystem_records(Config(project_roots=[str(empty)]), scan_status=status)
        db.replace_source("filesystem", rows, **status)
        self.assertEqual(0, db.source_count("filesystem"))

    def test_walk_error_disables_deletion(self):
        folder = self.root / "protected"
        folder.mkdir()
        def failed_walk(*args, **kwargs):
            kwargs["onerror"](PermissionError("fixture access denied"))
            return iter(())
        status = {}
        with patch.object(sources.os, "walk", failed_walk):
            sources.filesystem_records(Config(project_roots=[str(folder)]), scan_status=status)
        self.assertFalse(status["allow_delete"])

    def test_empty_adapter_preserves_previous_index(self):
        db.replace_source("fixture", [self.record()])
        with patch.object(indexer, "SOURCES", {"fixture": lambda: []}), patch.object(sources, "filesystem_records", return_value=[]):
            indexer.sync(Config(project_roots=[]), force=True)
        self.assertEqual(1, db.source_count("fixture"))
        self.assertEqual("unavailable", next(row for row in db.status() if row["source"] == "fixture")["state"])

    def test_ask_redacts_all_outbound_fields(self):
        secret = "fictional-audit-secret"
        row = self.record(id="fixture", source="fixture", title=f"password={secret}", body=f"token={secret}", project_title=f"api_key={secret}")
        with patch.object(ai, "retrieve", return_value=[row]), patch.object(ai, "_post", return_value={"choices": [{"message": {"content": "done"}}]}) as post:
            ai.ask(Config(ai={"enabled": True, "model": "fixture", "base_url": "http://fixture"}), f"password={secret}")
            self.assertNotIn(secret, json.dumps(post.call_args.args[2]))
        self.assertNotIn(secret, ai.redact(f'{{"token": "{secret}"}} Authorization: Bearer {secret}'))
        self.assertIn(secret, ai.outbound_text(Config(full_memory_mode=True), f"token={secret}"))

    def test_bad_embedding_responses_fall_back(self):
        db.replace_source("fixture", [self.record()])
        row = db.search("")[0]
        db.save_embedding(row["id"], row["content_hash"], "embed", struct.pack("<2f", 1, 0), 2)
        config = Config(embedding={"enabled": True, "model": "embed", "base_url": "http://fixture"})
        for vector in ([], [0, 0], [float("nan"), 1], ["bad", 1], [1e100, 0]):
            with self.subTest(vector=vector), patch.object(ai, "_post", return_value={"data": [{"index": 0, "embedding": vector}]}):
                self.assertEqual(row["id"], ai.retrieve(config, "fixture")[0]["id"])

    def test_semantic_top_k_and_malformed_stored_vector(self):
        config = Config(embedding={"enabled": True, "model": "embed", "base_url": "http://fixture"})
        db.replace_source("fixture", [self.record(locator=f"C:/{i}", title=str(i)) for i in range(4)])
        for i, row in enumerate(db.search("")):
            db.save_embedding(row["id"], row["content_hash"], "embed", struct.pack("<2f", 1, i), 2)
        with patch.object(ai, "embed_texts", return_value=[[1, 0]]):
            found = ai.semantic_search(config, "fixture", 2)
        self.assertEqual(2, len(found))
        self.assertGreaterEqual(found[0]["cosine_score"], found[1]["cosine_score"])
        self.assertEqual(-1, ai._cosine([1, 0], b"bad", 2))

    def test_mcp_error_keeps_request_id_and_rejects_unknown_tool(self):
        for params in ({"name":"missing", "arguments":{}}, {"name":"memory_search", "arguments":{"query":"x", "limit":"bad"}}):
            request = {"jsonrpc":"2.0", "id":9, "method":"tools/call", "params":params}
            output = io.StringIO()
            with patch.object(mcp.sys, "stdin", io.StringIO(json.dumps(request)+"\n")), patch.object(mcp.sys, "stdout", output):
                mcp.main()
            result = json.loads(output.getvalue())
            self.assertEqual(9, result["id"])
            self.assertEqual(-32602, result["error"]["code"])

    def test_http_access_boundary_and_live_prompt(self):
        config = Config(port=19001, api_token_env="FIXTURE_ACCESS_TOKEN")
        with patch.dict("os.environ", {"FIXTURE_ACCESS_TOKEN":"synthetic-token"}), patch.object(indexer.SyncLoop, "start"), TestClient(create_app(config), base_url="http://127.0.0.1:19001") as client:
            self.assertEqual(200, client.get("/api/health/live").status_code)
            self.assertEqual(401, client.get("/api/search").status_code)
            headers = {"Authorization":"Bearer synthetic-token"}
            prompt = client.get("/api/agent-prompt", headers=headers).json()
            self.assertFalse(prompt["full_memory_mode"])
            self.assertIn(":19001/", prompt["prompt"])
            self.assertEqual(403, client.post("/api/sync", headers={**headers, "Origin":"https://untrusted.example"}).status_code)
            self.assertEqual(400, client.get("/", headers={"Host":"untrusted.example"}).status_code)
            self.assertEqual(200, client.get("/static/app.js").status_code)

    def test_web_export_roots_configurable(self):
        from memory_hub.web_export_common import export_root
        self.assertEqual(self.root, export_root("gemini", Config(web_export_roots={"gemini":str(self.root)})))

    def test_invalid_config_does_not_silently_reset(self):
        from memory_hub import config
        (self.root / "config.json").write_text('{"port":"invalid"}', encoding="utf-8")
        with patch.object(config, "APP_DATA", self.root), self.assertRaises(ValueError):
            config.load_config()
