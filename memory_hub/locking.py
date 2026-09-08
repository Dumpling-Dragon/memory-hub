"""Nonblocking OS file locks, released automatically if a worker exits."""
from __future__ import annotations

import errno
import os

from .config import APP_DATA


def try_lock(name: str):
    APP_DATA.mkdir(parents=True, exist_ok=True)
    handle = (APP_DATA / f"{name}.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError as error:
        handle.close()
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        raise


def unlock(handle) -> None:
    # Closing the handle releases the byte-range lock/flock on either platform.
    if handle is not None:
        handle.close()
