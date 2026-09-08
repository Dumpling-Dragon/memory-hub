from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .config import Config
from .db import connect, replace_source, source_count, source_error, source_workspace_paths
from . import sources
from .web_import import web_records
from .locking import try_lock, unlock


SOURCES: dict[str, Callable[[], list[dict]]] = {
    "codex": sources.codex_records,
    "claude_code": sources.claude_records,
    "hermes": sources.hermes_records,
    "workbuddy": sources.workbuddy_records,
    "qoder": sources.qoder_records,
    "qoderwork_cn": sources.qoderwork_cn_records,
    "pi": sources.pi_records,
    "deepseek_harness": sources.dsh_records,
    "zcode": sources.zcode_records,
    "gemini": lambda: web_records("gemini"),
    "yuanbao": lambda: web_records("yuanbao"),
}


_SYNC_STATE_LOCK = threading.Lock()
_SYNC_RUNNING = False
_SYNC_PROCESS_LOCK = None
_SYNC_STARTED_AT: float | None = None
_SYNC_FINISHED_AT: float | None = None
_SYNC_LAST_RESULTS: dict[str, int | str] = {}
_SOURCE_FINGERPRINTS: dict[str, str] = {}


def _begin_sync() -> bool:
    global _SYNC_RUNNING, _SYNC_STARTED_AT, _SYNC_PROCESS_LOCK
    with _SYNC_STATE_LOCK:
        if _SYNC_RUNNING:
            return False
        _SYNC_PROCESS_LOCK = try_lock("sync")
        if _SYNC_PROCESS_LOCK is None:
            return False
        _SYNC_RUNNING = True
        _SYNC_STARTED_AT = time.time()
        return True


def _finish_sync(results: dict[str, int | str]) -> None:
    global _SYNC_RUNNING, _SYNC_FINISHED_AT, _SYNC_LAST_RESULTS, _SYNC_PROCESS_LOCK
    with _SYNC_STATE_LOCK:
        unlock(_SYNC_PROCESS_LOCK)
        _SYNC_PROCESS_LOCK = None
        _SYNC_RUNNING = False
        _SYNC_FINISHED_AT = time.time()
        _SYNC_LAST_RESULTS = dict(results)


def sync_status() -> dict:
    with _SYNC_STATE_LOCK:
        return {
            "running": _SYNC_RUNNING,
            "started_at": _SYNC_STARTED_AT,
            "finished_at": _SYNC_FINISHED_AT,
            "last_results": dict(_SYNC_LAST_RESULTS),
        }


def sync(config: Config, *, _reserved: bool = False, force: bool = False, stop_event=None) -> dict[str, int | str]:
    if not _reserved and not _begin_sync():
        return {"sync": "already_running"}
    results: dict[str, int | str] = {}
    try:
        sources.cleanup_stale_snapshots()
        discovered_roots: list[str] = []
        for name, reader in SOURCES.items():
            if stop_event is not None and stop_event.is_set():
                results["sync"] = "cancelled"
                return results
            try:
                fingerprint = sources.source_fingerprint(name, config) if name in {"gemini", "yuanbao"} else sources.source_fingerprint(name)
                if not force and fingerprint is not None and _SOURCE_FINGERPRINTS.get(name) == fingerprint:
                    results[name] = source_count(name)
                    discovered_roots.extend(source_workspace_paths(name))
                    continue
                records = web_records(name, config) if name in {"gemini", "yuanbao"} else reader()
                if stop_event is not None and stop_event.is_set():
                    results["sync"] = "cancelled"
                    return results
                if not records:
                    previous_count = source_count(name)
                    source_error(name, "No records available; previous index retained." if previous_count else "No local records configured or found.",
                                 state="unavailable" if previous_count else "not_configured")
                    results[name] = previous_count
                    discovered_roots.extend(source_workspace_paths(name))
                    continue
                results[name] = replace_source(name, records, "metadata only" if name == "qoder" else "ok")
                discovered_roots.extend(record.get("workspace_path") for record in records if record.get("workspace_path"))
                if fingerprint is not None:
                    _SOURCE_FINGERPRINTS[name] = fingerprint
            except Exception as error:  # A broken client format must not stop the rest.
                source_error(name, error, state="not_configured" if isinstance(error, FileNotFoundError) and not source_count(name) else "error")
                results[name] = f"error: {error}"
                discovered_roots.extend(source_workspace_paths(name))
        try:
            scan_status: dict = {}
            file_records = sources.filesystem_records(config, discovered_roots, scan_status=scan_status, stop_event=stop_event)
            if stop_event is not None and stop_event.is_set():
                results["sync"] = "cancelled"
                return results
            results["filesystem"] = replace_source(
                "filesystem", file_records, scan_status.get("detail", "ok"), state=scan_status.get("state", "ok"),
                allow_delete=scan_status.get("allow_delete", True)
            )
        except InterruptedError:
            results["sync"] = "cancelled"
            return results
        except Exception as error:
            source_error("filesystem", error)
            results["filesystem"] = f"error: {error}"
        # Remove health rows left by adapters that are no longer installed. This
        # keeps the UI/API truthful without touching documents from active sources.
        active_sources = tuple((*SOURCES.keys(), "filesystem", "codex_archive", "claude_code_archive", "hermes_archive"))
        placeholders = ",".join("?" for _ in active_sources)
        with connect() as conn:
            conn.execute(f"DELETE FROM source_status WHERE source NOT IN ({placeholders})", active_sources)
        return results
    finally:
        _finish_sync(results)


def start_sync(config: Config) -> dict[str, str]:
    """Start one background reconciliation without allowing overlapping runs."""
    if not _begin_sync():
        return {"state": "already_running"}
    thread = threading.Thread(
        target=sync,
        kwargs={"config": config, "_reserved": True},
        name="memory-hub-manual-sync",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        _finish_sync({"sync": "failed_to_start"})
        raise
    return {"state": "started"}


class SyncLoop:
    def __init__(self, config: Config):
        self.config = config
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="memory-hub-sync", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        if self.stop_event.wait(max(0, self.config.startup_sync_delay_seconds)):
            return
        sync(self.config, stop_event=self.stop_event)
        while not self.stop_event.wait(self.config.sync_interval_seconds):
            sync(self.config, stop_event=self.stop_event)
