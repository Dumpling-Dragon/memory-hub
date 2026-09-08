from __future__ import annotations

import argparse
import json
import sys
import threading

import uvicorn

from .config import load_config
from .db import init_db, search
from .indexer import start_sync, sync
from . import ai


def _configure_console() -> None:
    """Keep JSON output usable in legacy Windows consoles without affecting GUI builds."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(prog="memory", description="Search local Agent work memory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="Read all configured sources now")
    embeddings = sub.add_parser("embed", help="Create embeddings for new or changed records")
    embeddings.add_argument("--limit", type=int, default=100)
    embeddings.add_argument("--all", action="store_true", help="Continue in batches until all pending records are embedded")
    embeddings.add_argument("--retry-seconds", type=int, default=5, help="Wait before retrying a failed batch in --all mode")
    embeddings.add_argument("--pause-seconds", type=int, default=0, help="Pause after each successful batch in --all mode")
    for name in ("search", "continue"):
        command = sub.add_parser(name, help=f"{name} local memory")
        command.add_argument("query")
        command.add_argument("--source")
        command.add_argument("--owner")
        command.add_argument("--limit", type=int, default=12)
    ask = sub.add_parser("ask", help="Answer from local records with cited sources")
    ask.add_argument("question")
    ask.add_argument("--limit", type=int, default=10)
    serve = sub.add_parser("serve", help="Start local service")
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--no-desktop", action="store_true", help="Disable optional tray icon")
    args = parser.parse_args(argv)
    config = load_config()
    init_db()
    if args.command == "sync":
        print(json.dumps(sync(config, force=True), ensure_ascii=False, indent=2))
        return 0
    if args.command == "embed":
        completed = 0
        while True:
            try:
                result = ai.sync_embeddings(config, args.limit)
                completed += result["embedded"]
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if not args.all or result["remaining"] == 0:
                    return 0
                if args.pause_seconds:
                    import time
                    time.sleep(max(1, args.pause_seconds))
            except ai.AIConfigError as error:
                if not args.all:
                    print(str(error), file=sys.stderr)
                    return 2
                print(f"Embedding batch failed; retrying in {args.retry_seconds}s: {error}", file=sys.stderr, flush=True)
                import time
                time.sleep(max(1, args.retry_seconds))
    if args.command in {"search", "continue"}:
        results = search(args.query, args.source, None, args.limit, args.owner)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ask":
        try:
            print(json.dumps(ai.ask(config, args.question, args.limit), ensure_ascii=False, indent=2))
            return 0
        except ai.AIConfigError as error:
            print(str(error), file=sys.stderr)
            return 2
    if not args.no_desktop and sys.platform == "win32":
        from .tray import start_tray
        start_tray(lambda: start_sync(config), config)
    # Windowed PyInstaller builds have no stdout/stderr; Uvicorn's color formatter expects isatty().
    from .app import create_app
    uvicorn.run(create_app(config), host=config.host, port=config.port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
