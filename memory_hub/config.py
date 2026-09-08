from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "MemoryHub"
WORKSPACE = Path.home() / "Desktop" / "codex space" / "按对话归类"


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 17888
    sync_interval_seconds: int = 300
    startup_sync_delay_seconds: int = 15
    max_file_bytes: int = 2_000_000
    full_memory_mode: bool = False
    api_token_env: str = ""
    web_export_roots: dict[str, str] | None = None
    project_roots: list[str] | None = None
    embedding: dict | None = None
    reranker: dict | None = None
    ai: dict | None = None

    def __post_init__(self) -> None:
        if self.project_roots is None:
            self.project_roots = [str(WORKSPACE)] if WORKSPACE.is_dir() else []
        if self.web_export_roots is None:
            self.web_export_roots = {}
        if self.embedding is None:
            self.embedding = {"enabled": False}
        if self.reranker is None:
            self.reranker = {"enabled": False}
        if self.ai is None:
            self.ai = {"enabled": False}
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not isinstance(self.project_roots, list) or any(not isinstance(root, str) for root in self.project_roots):
            raise ValueError("project_roots must be a list of paths")
        if not isinstance(self.web_export_roots, dict) or any(not isinstance(value, str) for value in self.web_export_roots.values()):
            raise ValueError("web_export_roots must map platforms to paths")
        if type(self.full_memory_mode) is not bool:
            raise ValueError("full_memory_mode must be true or false")
        if not isinstance(self.host, str) or not isinstance(self.api_token_env, str):
            raise ValueError("host and api_token_env must be strings")
        for field in ("embedding", "reranker", "ai"):
            if not isinstance(getattr(self, field), dict):
                raise ValueError(f"{field} must be an object")
        for field in ("sync_interval_seconds", "startup_sync_delay_seconds", "max_file_bytes"):
            if type(getattr(self, field)) is not int or getattr(self, field) < (0 if field == "startup_sync_delay_seconds" else 1):
                raise ValueError(f"invalid {field}")


def local_url(config: Config) -> str:
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(config.host, config.host)
    return f"http://{'[' + host + ']' if ':' in host else host}:{config.port}"


def load_config() -> Config:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    path = APP_DATA / "config.json"
    if not path.exists():
        config = Config()
        path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {key: value for key, value in raw.items() if key in Config.__dataclass_fields__}
        config = Config(**allowed)
        # A previous Windows encoding failure could persist CJK path segments
        # as literal question marks. Recover only that unmistakable corruption;
        # leave ordinary missing/custom roots visible to the source status.
        if config.project_roots and any("????" in str(value) for value in config.project_roots) and WORKSPACE.exists():
            config.project_roots = [str(WORKSPACE)]
            path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        return config
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"Invalid Memory Hub configuration: {path}: {error}") from error
