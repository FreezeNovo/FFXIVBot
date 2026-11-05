import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ff14_app.runtime_lock import RuntimeLock


class RuntimeLockTests(unittest.TestCase):
    def test_second_process_is_rejected_and_lock_releases_after_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'runtime.lock'
            code = 'from pathlib import Path; from ff14_app.runtime_lock import RuntimeLock; import sys\nwith RuntimeLock(Path(sys.argv[1])): pass'
            command = [sys.executable, '-c', code, str(path)]
            with RuntimeLock(path):
                child = subprocess.run(command, text=True, capture_output=True, timeout=10)
                self.assertNotEqual(child.returncode, 0)
                self.assertIn('无法取得运行锁', child.stderr)
            subprocess.run(command, check=True, capture_output=True, timeout=10)
