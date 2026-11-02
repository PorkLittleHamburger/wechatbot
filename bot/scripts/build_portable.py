from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path

from wechat_onebot import __version__


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT.parent / "dist"
PYTHON_VERSION = "3.13.9"
PYTHON_ARCHIVE = f"python-{PYTHON_VERSION}-embed-amd64.zip"


def ignore(directory: str, names: list[str]):
    blocked = {".git", "__pycache__", ".pytest_cache", ".venv", ".runtime", "data", "logs"}
    return [name for name in names if name in blocked or name.endswith(".egg-info")]


def copy_tree(source: Path, destination: Path):
    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


def bundle_python(target: Path) -> None:
    cache = Path(os.environ.get("WECHAT_BUILD_CACHE", str(DIST / ".cache")))
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / PYTHON_ARCHIVE
    if not archive.is_file():
        urllib.request.urlretrieve(
            f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ARCHIVE}", archive
        )
    runtime = target / "python"
    runtime.mkdir()
    with zipfile.ZipFile(archive) as package:
        package.extractall(runtime)
    (runtime / "python313._pth").write_text(
        "python313.zip\n.\nLib\\site-packages\n..\\src\n", encoding="utf-8"
    )
    requirements = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--no-compile",
         "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313",
         "--platform", "win_amd64", "--target",
         str(runtime / "Lib" / "site-packages"), "-r", str(ROOT / "requirements-runtime.lock")],
        check=True,
    )
    subprocess.run(
        [str(runtime / "python.exe"), "-B", "-c",
         "import fastapi, uvicorn, cryptography, PIL, wechat_onebot; print(wechat_onebot.__version__)"],
        cwd=target, check=True,
    )


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = DIST / f"wechat-onebot-portable-{stamp}"
    target.mkdir(parents=True, exist_ok=False)

    copy_tree(ROOT / "src", target / "src")
    copy_tree(ROOT / "vendor", target / "vendor")
    copy_tree(ROOT / "webui", target / "webui")
    copy_tree(ROOT / "assets", target / "assets")
    bundle_python(target)
    (target / "scripts").mkdir()
    for name in ("run_real.ps1", "run_mock.ps1", "wechat-onebot.ps1", "autostart.ps1", "run_launcher.ps1"):
        shutil.copy2(ROOT / "scripts" / name, target / "scripts" / name)
    for name in ("THIRD_PARTY_NOTICES.md", "pyproject.toml", "config.example.json",
                 "start.cmd", "launcher.cmd", "enable-autostart.cmd", "disable-autostart.cmd"):
        shutil.copy2(ROOT / name, target / name)
    shutil.copy2(ROOT / "PORTABLE_README.md", target / "README.md")
    if (ROOT / "docs").is_dir():
        copy_tree(ROOT / "docs", target / "docs")

    helper_root = ROOT / "vendor" / "helper"
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
        "entrypoint": "start.cmd",
        "config_example": "config.example.json",
    }
    (target / "BUILD_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    (target / "START_HERE.txt").write_text(
        "WeChat OneBot portable build\n"
        "1. Sign in to a supported Windows WeChat build.\n"
        "2. Double-click start.cmd; Python and dependencies are included in this folder.\n"
        "3. Open http://127.0.0.1:5099, enter the first-login token, and set a password.\n"
        "4. Select an account and save OneBot endpoints; they run even while WeChat is offline.\n"
        "   Start the WeChat bridge separately from the account page when needed.\n"
        "5. Enable autostart in WebUI Settings > Service to resume the last bridged account at Windows logon.\n"
        "   PC WeChat must log in first; this bridge does not provide a PC login QR API.\n"
        "The helper native bridge is non-commercial and requires the pinned WeChat build.\n",
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
