from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import uvicorn

from . import __version__
from .bridge import discover_logged_accounts
from .config import AppConfig, ConfigError, update_account
from .core.runtime import RuntimeManager
from .webui import create_webui_app


def _configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _build_manager(config: AppConfig) -> RuntimeManager:
    return RuntimeManager(config)


def _file_version(path: Path) -> str:
    if os.name != "nt":
        return ""
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return ""
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return ""
    fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
    ms, ls = fixed[2], fixed[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def _find_weixin_executables() -> list[Path]:
    candidates: list[Path] = []
    if os.name == "nt":
        import winreg
        keys = (
            (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\Weixin"),
        )
        for root, subkey in keys:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    install, _ = winreg.QueryValueEx(key, "InstallPath")
                    if isinstance(install, str) and install:
                        path = Path(install)
                        candidates.extend((path / "Weixin.exe", path / "WeChat.exe"))
            except OSError:
                pass
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if root:
            base = Path(root)
            candidates.extend((
                base / "Tencent" / "Weixin" / "Weixin.exe",
                base / "Tencent" / "WeChat" / "WeChat.exe",
            ))
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key not in seen and path.is_file():
            seen.add(key)
            result.append(path.resolve())
    return result


def command_doctor(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.config)
    helper_root = config.driver.helper_root
    executables = []
    for path in _find_weixin_executables():
        executables.append({"path": str(path), "version": _file_version(path)})
    compatibility_path = helper_root / "native" / "compatibility.json"
    compatibility = None
    if compatibility_path.is_file():
        compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
    supported = list(config.driver.supported_versions)
    matches = [row for row in executables if row["version"] in supported]
    native_hashes = {}
    hash_manifest_path = helper_root / "VENDORED_SHA256.json"
    if hash_manifest_path.is_file():
        try:
            manifest = json.loads(hash_manifest_path.read_text(encoding="utf-8"))
            for item in manifest:
                path = helper_root / item["RelativePath"]
                if path.is_file():
                    digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
                    native_hashes[item["RelativePath"]] = digest == item["SHA256"].upper()
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            native_hashes = {"manifest": False}
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == "nt" else False
    report = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "python_64bit": sys.maxsize > 2**32,
        "administrator": is_admin,
        "helper_root": str(helper_root),
        "helper_commit": (helper_root / "VENDORED_COMMIT.txt").read_text(encoding="utf-8").strip()
            if (helper_root / "VENDORED_COMMIT.txt").is_file() else "",
        "helper_license": (helper_root / "LICENSE").is_file(),
        "native_bridge": (helper_root / "native" / "build_text" / "text_bridge.dll").is_file(),
        "native_bridge_hash_ok": bool(native_hashes) and all(native_hashes.values()),
        "native_bridge_hashes": native_hashes,
        "supported_versions": supported,
        "compatibility": compatibility,
        "wechat_installations": executables,
        "supported_wechat_found": bool(matches),
        "bound_account": config.driver.wechat_account,
        "driver_type": config.driver.type,
        "token_is_placeholder": config.onebot.access_token in ("", "change-this-token"),
        "webui_token_is_placeholder": config.webui.access_token in ("", "change-this-webui-token"),
        "webui_enabled": config.webui.enabled,
        "webui_endpoint": f"http://{config.webui.host}:{config.webui.port}",
        "onebot_endpoint": f"http://{config.onebot.host}:{config.onebot.port}",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = all((
        sys.platform == "win32",
        sys.maxsize > 2**32,
        is_admin,
        report["helper_license"],
        report["native_bridge"],
        report["native_bridge_hash_ok"],
        report["supported_wechat_found"],
        not report["token_is_placeholder"],
        not report["webui_token_is_placeholder"],
    ))
    return 0 if ok else 1


def command_bind(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.config)
    if os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin():
        raise ConfigError("账号识别与原生桥接需要管理员权限，请在提权后的 PowerShell 中运行 bind")
    accounts = discover_logged_accounts(config.driver.helper_root)
    if not accounts:
        print("没有发现已登录的电脑微信账号。请先打开微信并登录。")
        return 1
    for index, row in enumerate(accounts, 1):
        print(f"{index}. {row.nickname or '未读取到昵称'} | wxid={row.wxid} | pid={row.pid}")
    requested = getattr(args, "account", None)
    if requested:
        matches = [row for row in accounts if row.wxid == requested]
        if len(matches) != 1:
            print(f"未找到唯一匹配的已登录账号: {requested}")
            return 1
        selected = matches[0]
    elif len(accounts) == 1:
        selected = accounts[0]
    else:
        choice = input("请选择机器人账号序号: ").strip()
        if not choice.isdecimal() or not 1 <= int(choice) <= len(accounts):
            print("序号无效。")
            return 1
        selected = accounts[int(choice) - 1]
    update_account(config.path, account=selected.wxid, pid=selected.pid, nickname=selected.nickname)
    print(f"已绑定 {selected.nickname or selected.wxid} ({selected.wxid})，未注入微信。")
    return 0


def command_run(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.config)
    if args.mock:
        config = replace(config, driver=replace(config.driver, type="mock"))
    if config.driver.type == "helper" and os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin():
        raise ConfigError("helper 原生驱动需要管理员权限，请从提权后的 PowerShell 启动；协议开发可使用 run --mock")
    _configure_logging(args.log_level)
    manager = _build_manager(config)
    app = create_webui_app(config, manager)
    try:
        uvicorn.run(app, host=config.webui.host, port=config.webui.port,
                    log_level=args.log_level.lower(), access_log=args.access_log)
    finally:
        # The WebUI lifespan owns RuntimeManager shutdown.
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat-onebot", description="Windows 个人微信 OneBot v11 网关")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default="config.json", help="配置文件，默认 config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="启动 OneBot v11 服务")
    run.add_argument("--mock", action="store_true", help="强制使用 Mock 驱动，不连接微信")
    run.add_argument("--log-level", default="INFO", choices=["debug", "info", "warning", "error", "critical", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    run.add_argument("--access-log", action="store_true", help="输出 HTTP 访问日志")
    run.set_defaults(func=command_run)

    bind = sub.add_parser("bind", help="只读识别并绑定当前已登录微信账号")
    bind.add_argument("--account", help="多实例时按 wxid 非交互选择机器人账号")
    bind.set_defaults(func=command_bind)

    doctor = sub.add_parser("doctor", help="检查 Windows、微信版本、原生桥接和配置")
    doctor.set_defaults(func=command_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except ConfigError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        logging.getLogger("wechat-onebot.cli").exception("命令失败")
        print(f"错误: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
