import os
from typing import Optional

from loguru import logger

from app.utils import utils

_lock_path: Optional[str] = None
_acquired_by_this_process = False


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:  # noqa: BLE001 - unsupported on this platform; don't block startup over it
        return False
    return True


def acquire() -> bool:
    """
    Best-effort single-instance guard. uvicorn runs the ASGI startup event
    (which resumes in-flight projects and starts the scheduler) before it
    binds the listen socket - so a second `python main.py` invocation on an
    already-used port would otherwise still re-trigger those side effects
    against the shared database before failing to bind, racing real agent
    work against whatever instance is actually running. Returns False if
    another live process already holds the lock.
    """
    global _lock_path, _acquired_by_this_process
    _lock_path = os.path.join(utils.storage_dir(create=True), "aura.pid")

    if os.path.isfile(_lock_path):
        try:
            with open(_lock_path, "r") as f:
                existing_pid = int(f.read().strip())
        except (ValueError, OSError):
            existing_pid = None

        if existing_pid and existing_pid != os.getpid() and _pid_is_alive(existing_pid):
            logger.error(
                f"another Aura-Video instance is already running (pid {existing_pid}); "
                "skipping startup side effects (crash recovery, scheduler) for this process"
            )
            return False

    with open(_lock_path, "w") as f:
        f.write(str(os.getpid()))
    _acquired_by_this_process = True
    return True


def release() -> None:
    global _acquired_by_this_process
    if _acquired_by_this_process and _lock_path and os.path.isfile(_lock_path):
        try:
            os.remove(_lock_path)
        except OSError:
            pass
    _acquired_by_this_process = False
