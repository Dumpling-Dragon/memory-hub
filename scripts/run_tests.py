"""Run offline tests using an isolated store, never the user's Memory Hub."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
work = root / ".test-work"
work.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=work) as directory:
    os.environ["LOCALAPPDATA"] = directory
    os.environ["APPDATA"] = directory
    tempfile.tempdir = directory
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
