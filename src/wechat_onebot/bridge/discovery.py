from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .models import WeChatLogin

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import AppConfig


def discover_logged_accounts(helper_root: str | Path) -> list[WeChatLogin]:
    """Explicit, event-driven account discovery.

    This function is intentionally not wrapped in a polling loop. Callers invoke
    it on startup, manual refresh, or a trusted login/process event.
    """
    if sys.platform != "win32":
        return []
    root = Path(helper_root).resolve()
    if not (root / "wxbot_framework" / "startup.py").is_file():
        raise RuntimeError(f"helper 目录不完整: {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from wxbot_framework.startup import scan_logged_accounts

    rows = scan_logged_accounts(root)
    return [
        WeChatLogin(pid=int(row["pid"]), wxid=str(row["wxid"]),
                    nickname=str(row.get("name") or ""))
        for row in rows
    ]


def discover_sdk_accounts(config: "AppConfig") -> list[WeChatLogin]:
    """Read-only discovery through the independent ``wechatbot_api`` SDK.

    Only processes whose build is registered and whose account identity was
    verified are returned; unsupported builds stay visible through the error
    detail instead of being reported as logged-out.
    """
    return sdk_discovery_report(config)[0]


def sdk_discovery_report(config: "AppConfig") -> tuple[list[WeChatLogin], list[dict[str, str]]]:
    """Return verified SDK accounts and per-candidate diagnostics.

    Diagnostics keep the build version and error code so the caller can explain
    an unsupported or not-yet-logged-in client instead of reporting a generic
    "not logged in" failure.
    """
    if sys.platform != "win32":
        return [], []
    try:
        import wechatbot_api
    except ImportError as error:  # pragma: no cover - packaging check
        raise RuntimeError(
            "未安装 wechatbot_api 运行时；请安装 vendor/wechatbot_api 下的 wheel"
        ) from error
    rows: list[WeChatLogin] = []
    diagnostics: list[dict[str, str]] = []
    for result in wechatbot_api.Client().discover():
        build = getattr(result, "build", None)
        account = getattr(result, "account", None)
        error = getattr(result, "error", None)
        if account is None or error is not None:
            diagnostics.append({
                "pid": str(getattr(result, "pid", "")),
                "error": (getattr(error, "value", None) or str(error)) if error is not None else "",
                "version": str(getattr(build, "version", "") or ""),
                "detail": str(getattr(result, "detail", "") or ""),
            })
            continue
        rows.append(WeChatLogin(
            pid=int(result.pid), wxid=str(account.wxid),
            nickname=str(getattr(account, "wechat_name", "") or ""),
            version=str(getattr(build, "version", "") or ""),
            extra={
                "bridge_supported": bool(getattr(result, "bridge_supported", False)),
                "detail": str(getattr(result, "detail", "") or ""),
                "sha256": str(getattr(build, "sha256", "") or ""),
            },
        ))
    return rows, diagnostics


def discover_accounts(config: "AppConfig") -> list[WeChatLogin]:
    """Dispatch discovery by driver without changing the helper call contract."""
    if config.driver.type == "sdk":
        return discover_sdk_accounts(config)
    if config.driver.type == "mock":
        return []
    return discover_logged_accounts(config.driver.helper_root)
