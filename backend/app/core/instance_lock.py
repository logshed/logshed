"""
A lock file beside the database, so a restore never replaces the database under a running LogShed.

The web process holds a shared lock on `.logshed.lock` while it runs; the `restore` command takes
it exclusively, and fails at once when LogShed holds it. Shared locks do not conflict, so nothing
changes for other processes. The lock is an flock(2) lock: the kernel drops it when the process
ends, so a crash never leaves it behind, and containers that mount the same folder see each other's
locks.
"""

import fcntl
import logging
import os
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

LOCK_NAME = ".logshed.lock"


class InstanceLockBusy(RuntimeError):
    """The lock is held in a way that conflicts with the one asked for."""


def lock_path(db_path: Union[str, Path]) -> Path:
    return Path(db_path).parent / LOCK_NAME


def acquire(db_path: Union[str, Path], exclusive: bool) -> Optional[int]:
    """
    Take the lock without waiting and return its file descriptor (release it with `release`).
    Raises InstanceLockBusy when it conflicts. Returns None when the lock file cannot be opened
    (a read-only folder, for example): the caller carries on without it.
    """
    path = lock_path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o640)
    except OSError as e:
        logger.warning(f"Could not open the lock file {path}: {e}")
        return None
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise InstanceLockBusy(str(path)) from None
    except OSError as e:
        os.close(fd)
        logger.warning(f"Could not lock {path}: {e}")
        return None
    return fd


def release(fd: Optional[int]) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
