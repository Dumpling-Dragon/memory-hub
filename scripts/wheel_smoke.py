"""Check an unpacked wheel outside the source tree with an isolated runtime store."""
import os
import sys
import tempfile
import subprocess
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
work = root / ".test-work"
work.mkdir(exist_ok=True)
wheel = max((root / "dist").glob("*.whl"), key=lambda path: path.stat().st_mtime)
with tempfile.TemporaryDirectory(dir=work) as directory:
    installed = Path(directory) / "installed"
    with zipfile.ZipFile(wheel) as archive:
        assert "memory_hub/static/index.html" in archive.namelist()
        assert "memory_hub/static/api.js" in archive.namelist()
        archive.extractall(installed)
    env = dict(os.environ, PYTHONPATH=str(installed), LOCALAPPDATA=directory, APPDATA=directory, PYTHONIOENCODING="utf-8")
    code = "from fastapi.testclient import TestClient; from memory_hub.app import app; from memory_hub.cli import main; c=TestClient(app, base_url='http://127.0.0.1'); assert c.get('/').status_code==200; assert c.get('/static/app.js').status_code==200; main(['search','fixture'])"
    subprocess.run([sys.executable, "-B", "-c", code], env=env, cwd=directory, check=True)
print("Wheel imports, static resources and CLI search passed.")
