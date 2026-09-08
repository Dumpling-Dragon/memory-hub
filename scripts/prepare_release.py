"""Copy an explicit source allowlist into a clean, independently publishable tree."""
import hashlib
import json
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / "release" / "github-memory-hub"
target.mkdir(parents=True, exist_ok=True)
files = ["README.md", "LICENSE", "MANIFEST.in", "pyproject.toml", "requirements.txt", "config.example.json", ".gitignore", ".gitattributes",
         "constraints-windows-py311.txt", "memory.ps1", "sync-memory.ps1", "refresh-web-memory.ps1"]
patterns = {"memory_hub": ["*.py", "static/*.html", "static/*.css", "static/*.js"],
            "tests": ["*.py", "*.cjs"], "scripts": ["*.py", "*.ps1", "*.cmd"],
            "docs": ["*.md"], "skills": ["*/SKILL.md"],
            "desktop-control": ["*.cs", "*.ps1", "app.manifest", "README.md"],
            ".github": ["workflows/*.yml"]}
for directory, globs in patterns.items():
    for pattern in globs:
        files.extend(path.relative_to(root).as_posix() for path in (root / directory).glob(pattern) if path.is_file())
files.append("PROMPT-给其他AI.md")
for name in sorted(set(files)):
    source = root / name
    if not source.is_file():
        raise FileNotFoundError(f"Required release source missing: {name}")
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
(target / "AGENTS.md").write_text(
    "# Memory Hub maintenance\n\nPreserve user runtime bindings and full_memory_mode preferences.\n"
    "Keep one local database and reuse existing source connectors.\n"
    "Run scripts/run_tests.py for isolated offline tests; never test against personal chat stores.\n"
    "Do not commit databases, exports, credentials, logs, or build artifacts.\n",
    encoding="utf-8")
files.append("AGENTS.md")
manifest = [{"path": name, "bytes": (target/name).stat().st_size,
             "sha256": hashlib.sha256((target/name).read_bytes()).hexdigest()} for name in sorted(set(files))]
(root / "release" / "source-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Prepared {len(manifest)} source files ({sum(row['bytes'] for row in manifest):,} bytes).")
