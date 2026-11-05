"""同一数据目录只允许一个运行实例，防止启动恢复误接管在途消息。"""
from __future__ import annotations

import os
from pathlib import Path


class RuntimeLock:
    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            if self.path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise RuntimeError('无法取得运行锁；请确认没有其他 FFXIVBot 实例使用同一数据目录') from error
        self._file = handle
        return self

    def __exit__(self, *args):
        if self._file is not None:
            self._file.close()
            self._file = None
