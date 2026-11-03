"""OS-held, nonblocking bridge ownership for one target WeChat PID.

Lock files are permanent rendezvous points: unlinking one could let contenders
lock different inodes. Descriptors are explicitly released, never finalized, so
discarding a failed driver's guard cannot silently permit another injection.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


class ProcessLockError(RuntimeError):
    """The target PID could not be exclusively acquired."""


class BridgeProcessLock:
    def __init__(self, pid: int):
        if type(pid) is not int or pid <= 0:
            raise ValueError("微信 PID 必须是正整数")
        self.pid = pid
        root = Path(tempfile.gettempdir()) / "wechat-onebot-bridge-locks"
        # Windows normally provides a per-user TEMP; POSIX /tmp is shared.
        if os.name != "nt":
            root /= str(os.getuid())
        self.path = root / f"{pid}.lock"
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self.held:
            raise ProcessLockError(f"微信 PID={self.pid} 的桥接锁已由此实例持有")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                # Windows permits locking beyond EOF. Do not write/truncate a
                # byte that may already be locked by another open descriptor.
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as error:
            os.close(fd)
            if isinstance(error, OSError):
                raise ProcessLockError(
                    f"无法独占微信 PID={self.pid} 的桥接（可能已被其他驱动或进程占用）: {error}"
                ) from error
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        # Keep the descriptor if unlock fails; callers can retry safely.
        if os.name == "nt":
            import msvcrt

            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None
