"""Windows logon task and the last successfully bridged account."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..config import _atomic_write_json


def last_bridge_path(data_dir: Path) -> Path:
    return data_dir / "startup.json"


def load_last_bridge(data_dir: Path) -> str | None:
    path = last_bridge_path(data_dir)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    account = value.get("last_account")
    if not isinstance(account, str) or not account:
        raise ValueError("startup.json 中的 last_account 无效")
    return account


def save_last_bridge(data_dir: Path, account: str) -> None:
    _atomic_write_json(last_bridge_path(data_dir), {"last_account": account})


def autostart_task(action: str) -> dict[str, bool]:
    if os.name != "nt":
        raise RuntimeError("开机自启仅支持 Windows")
    shell = (os.environ.get("WECHAT_ONEBOT_PWSH") or shutil.which("pwsh") or
             str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PowerShell/7/pwsh.exe"))
    if not Path(shell).is_file():
        shell = shutil.which("powershell.exe") or "powershell.exe"
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "autostart.ps1"
    mode = {"status": "-Status", "install": "-Install", "remove": "-Remove"}[action]
    args = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), mode]
    if action == "status":
        args.append("-Json")
    else:
        args.append("-NoElevate")
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "计划任务操作失败")
    if action != "status":
        return autostart_task("status")
    return json.loads(result.stdout)
