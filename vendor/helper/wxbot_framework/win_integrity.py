"""Windows integrity-label helpers for cross-process media handoff."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys


def _is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def make_low_integrity(path: str | os.PathLike[str]) -> None:
    """Allow the normal-integrity WeChat process to write into an elevated temp dir.

    The helper client must be elevated to load the native bridge, but the native
    download callback executes inside WeChat. Windows no-write-up prevents that
    process from creating the downloaded file in a high-integrity workspace.
    Lowering only this per-message workspace preserves account-directory access
    boundaries while allowing the handoff.
    """
    if not _is_elevated():
        return
    value = os.fspath(path)
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("integrity target must be a non-empty local path")
    resolved = Path(value).resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("integrity target must be an existing directory")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["icacls", str(resolved), "/setintegritylevel", "Low"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise OSError(f"failed to lower integrity label for media workspace: {detail}")
