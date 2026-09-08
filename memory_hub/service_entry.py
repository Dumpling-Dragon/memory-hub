"""Console-free service entry used by the native desktop control window."""
from __future__ import annotations

import sys
import traceback

from .config import APP_DATA


def main() -> int:
    log_dir = APP_DATA / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # Write directly to a file, so closing the control window cannot break a pipe.
    with (log_dir / "control-service.log").open("a", encoding="utf-8", buffering=1) as log:
        previous_streams = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        try:
            from .cli import main as run

            return run(["serve", "--no-desktop", "--no-browser"])
        except Exception:
            traceback.print_exc()
            return 1
        finally:
            sys.stdout, sys.stderr = previous_streams


if __name__ == "__main__":
    raise SystemExit(main())
