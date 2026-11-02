from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from wechat_onebot import __version__
from wechat_onebot.config import AppConfig


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def ignore(directory: str, names: list[str]):
    blocked = {".git", "__pycache__", ".pytest_cache", ".venv", ".runtime", "data", "logs"}
    return [name for name in names if name in blocked or name.endswith(".egg-info")]


def copy_tree(source: Path, destination: Path):
    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = DIST / f"wechat-onebot-portable-{stamp}"
    target.mkdir(parents=True, exist_ok=False)

    copy_tree(ROOT / "src", target / "src")
    copy_tree(ROOT / "vendor", target / "vendor")
    copy_tree(ROOT / "webui", target / "webui")
    (target / "scripts").mkdir()
    for name in ("run_real.ps1", "run_mock.ps1", "wechat-onebot.ps1"):
        shutil.copy2(ROOT / "scripts" / name, target / "scripts" / name)
    for name in ("README.md", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "config.example.json"):
        shutil.copy2(ROOT / name, target / name)
    if (ROOT / "docs").is_dir():
        copy_tree(ROOT / "docs", target / "docs")

    app_config = AppConfig.load(ROOT / "config.json")
    helper_root = app_config.driver.helper_root
    helper_hashes = {}
    for path in (helper_root / "native" / "build_text", helper_root / "native" / "build"):
        if path.is_dir():
            for file in path.glob("*.dll"):
                helper_hashes[str(file.relative_to(ROOT)).replace("\\", "/")] = {
                    "size": file.stat().st_size,
                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                }
    manifest = {
        "name": "wechat-onebot-portable",
        "version": __version__,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "helper_commit": (helper_root / "VENDORED_COMMIT.txt").read_text(encoding="utf-8").strip(),
        "native_hashes": helper_hashes,
        "entrypoint": "scripts/run_real.ps1",
        "config_example": "config.example.json",
    }
    (target / "BUILD_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    (target / "START_HERE.txt").write_text(
        "WeChat OneBot portable build\n"
        "1. Copy config.example.json to config.json and edit tokens.\n"
        "2. Run scripts/run_real.ps1 from an elevated PowerShell.\n"
        "3. WebUI: http://127.0.0.1:5099\n"
        "4. OneBot HTTP: http://127.0.0.1:6700\n"
        "5. OneBot WS: ws://127.0.0.1:6701/ws\n"
        "The helper native bridge is non-commercial and requires the pinned WeChat build.\n",
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
