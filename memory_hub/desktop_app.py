from __future__ import annotations

import traceback

from memory_hub.cli import main
from memory_hub.config import APP_DATA


if __name__ == "__main__":
    try:
        raise SystemExit(main(["serve"]))
    except Exception:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        (APP_DATA / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
