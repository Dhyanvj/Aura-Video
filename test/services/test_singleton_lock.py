import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import singleton_lock
from app.utils import utils


class TestSingletonLock(unittest.TestCase):
    """
    Regression test: uvicorn runs the ASGI startup event (crash recovery +
    scheduler) before it binds the listen socket. Without this guard, a
    second `python main.py` on an already-used port would still resume
    in-flight projects and start the scheduler - re-triggering real agent
    work against the shared database - before failing to bind and exiting.
    """

    def setUp(self):
        self._lock_path = os.path.join(utils.storage_dir(create=True), "aura.pid")
        if os.path.isfile(self._lock_path):
            os.remove(self._lock_path)
        singleton_lock._acquired_by_this_process = False

    def tearDown(self):
        singleton_lock._acquired_by_this_process = False
        if os.path.isfile(self._lock_path):
            os.remove(self._lock_path)

    def test_acquires_when_no_existing_lock(self):
        self.assertTrue(singleton_lock.acquire())
        with open(self._lock_path) as f:
            self.assertEqual(int(f.read().strip()), os.getpid())

    def test_refuses_when_another_live_process_holds_the_lock(self):
        other = subprocess.Popen(["sleep", "30"])
        try:
            time.sleep(0.2)
            with open(self._lock_path, "w") as f:
                f.write(str(other.pid))
            self.assertFalse(singleton_lock.acquire())
        finally:
            other.terminate()
            other.wait()

    def test_acquires_when_lock_holder_pid_is_dead(self):
        with open(self._lock_path, "w") as f:
            f.write("999999999")  # not a real running pid
        self.assertTrue(singleton_lock.acquire())

    def test_release_only_removes_lock_if_this_process_acquired_it(self):
        # A process that never successfully acquired the lock (e.g. it saw
        # another live instance and bailed) must not delete that instance's
        # lock file on its own shutdown.
        with open(self._lock_path, "w") as f:
            f.write("123456")
        singleton_lock._acquired_by_this_process = False
        singleton_lock.release()
        self.assertTrue(os.path.isfile(self._lock_path))

    def test_release_removes_lock_when_acquired(self):
        self.assertTrue(singleton_lock.acquire())
        singleton_lock.release()
        self.assertFalse(os.path.isfile(self._lock_path))


if __name__ == "__main__":
    unittest.main()
