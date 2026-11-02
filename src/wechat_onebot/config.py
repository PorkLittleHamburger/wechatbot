from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__


class ConfigError(ValueError):
    pass


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def default_config() -> dict[str, Any]:
    return {
        "onebot": {
            "host": "127.0.0.1",
            "port": 6700,
            "access_token": secrets.token_urlsafe(24),
            "heartbeat_interval_seconds": 5.0,
        },
        "reverse_ws": [],
        "webui": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 5099,
            "access_token": secrets.token_urlsafe(24),
        },
        "driver": {
            "type": "helper",
            "helper_root": "vendor/helper",
            "wechat_account": None,
            "wechat_pid": None,
            "nickname": "",
            "wechat_data_directory": None,
            "supported_versions": ["4.1.15.13", "4.1.15.56"],
        },
        "storage": {
            "data_dir": "data",
            "media_dir": "data/media",
            "max_media_bytes": 50 * 1024 * 1024,
        },
    }


def safe_account_id(account: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", account or "unbound").strip("._")
    return value[:120] or "unbound"


def account_directories(config: "AppConfig", account: str | None = None) -> tuple[Path, Path]:
    name = safe_account_id(account or config.driver.wechat_account or "unbound")
    account_dir = config.storage.data_dir / "accounts" / name
    media_dir = config.storage.media_dir / name
    return account_dir, media_dir


def ensure_config(path: str | Path) -> Path:
    target = Path(path).resolve()
    if target.exists():
        return target
    _atomic_write_json(target, default_config())
    return target


@dataclass(frozen=True)
class OneBotConfig:
    host: str = "127.0.0.1"
    port: int = 6700
    access_token: str = ""
    heartbeat_interval_seconds: float = 5.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OneBotConfig":
        host = value.get("host", "127.0.0.1")
        port = value.get("port", 6700)
        token = value.get("access_token", "")
        heartbeat = value.get("heartbeat_interval_seconds", 5.0)
        if not isinstance(host, str) or not host.strip():
            raise ConfigError("onebot.host 必须是非空字符串")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError("onebot.port 必须是 1–65535 的整数")
        if not isinstance(token, str):
            raise ConfigError("onebot.access_token 必须是字符串")
        if type(heartbeat) not in (int, float) or float(heartbeat) < 0.5 or float(heartbeat) > 300:
            raise ConfigError("onebot.heartbeat_interval_seconds 必须在 0.5–300 之间")
        return cls(host=host, port=port, access_token=token,
                   heartbeat_interval_seconds=float(heartbeat))


@dataclass(frozen=True)
class ReverseWsConfig:
    url: str
    access_token: str = ""
    reconnect_seconds: float = 5.0
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReverseWsConfig":
        url = value.get("url", "")
        token = value.get("access_token", "")
        reconnect = value.get("reconnect_seconds", 5.0)
        enabled = value.get("enabled", True)
        if not isinstance(url, str) or not (url.startswith("ws://") or url.startswith("wss://")):
            raise ConfigError("reverse_ws.url 必须是 ws:// 或 wss:// 地址")
        if not isinstance(token, str):
            raise ConfigError("reverse_ws.access_token 必须是字符串")
        if type(reconnect) not in (int, float) or not 0.5 <= float(reconnect) <= 300:
            raise ConfigError("reverse_ws.reconnect_seconds 必须在 0.5–300 之间")
        if type(enabled) is not bool:
            raise ConfigError("reverse_ws.enabled 必须是布尔值")
        return cls(url=url, access_token=token, reconnect_seconds=float(reconnect), enabled=enabled)


@dataclass(frozen=True)
class DriverConfig:
    type: str = "helper"
    helper_root: Path = Path("vendor/helper")
    wechat_account: str = ""
    wechat_pid: int | None = None
    nickname: str = ""
    supported_versions: tuple[str, ...] = ("4.1.15.13", "4.1.15.56")
    wechat_data_directory: Path | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, base_dir: Path) -> "DriverConfig":
        driver_type = value.get("type", "helper")
        if driver_type not in ("helper", "mock"):
            raise ConfigError("driver.type 只能是 helper 或 mock")
        helper_root_raw = value.get("helper_root", "vendor/helper")
        if not isinstance(helper_root_raw, str) or not helper_root_raw.strip():
            raise ConfigError("driver.helper_root 必须是非空路径")
        helper_root = Path(helper_root_raw)
        if not helper_root.is_absolute():
            helper_root = (base_dir / helper_root).resolve()
        account = value.get("wechat_account") or ""
        pid = value.get("wechat_pid")
        nickname = value.get("nickname") or ""
        versions = value.get("supported_versions", ["4.1.15.13", "4.1.15.56"])
        data_directory_raw = value.get("wechat_data_directory")
        if not isinstance(account, str) or not isinstance(nickname, str):
            raise ConfigError("driver.wechat_account 和 nickname 必须是字符串")
        data_directory = None
        if data_directory_raw is not None:
            if not isinstance(data_directory_raw, str) or not data_directory_raw.strip():
                raise ConfigError("driver.wechat_data_directory 必须是非空路径或 null")
            data_directory = Path(data_directory_raw)
            if not data_directory.is_absolute():
                data_directory = (base_dir / data_directory).resolve()
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ConfigError("driver.wechat_pid 必须是正整数或 null")
        if not isinstance(versions, list) or not versions or any(not isinstance(item, str) for item in versions):
            raise ConfigError("driver.supported_versions 必须是非空字符串数组")
        return cls(type=driver_type, helper_root=helper_root, wechat_account=account.strip(),
                   wechat_pid=pid, nickname=nickname.strip(), supported_versions=tuple(versions),
                   wechat_data_directory=data_directory)


@dataclass(frozen=True)
class StorageConfig:
    data_dir: Path
    media_dir: Path
    max_media_bytes: int = 50 * 1024 * 1024

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, base_dir: Path) -> "StorageConfig":
        data_raw = value.get("data_dir", "data")
        media_raw = value.get("media_dir", "data/media")
        limit = value.get("max_media_bytes", 50 * 1024 * 1024)
        if not isinstance(data_raw, str) or not isinstance(media_raw, str):
            raise ConfigError("storage.data_dir 和 media_dir 必须是路径字符串")
        data_dir = Path(data_raw)
        media_dir = Path(media_raw)
        if not data_dir.is_absolute():
            data_dir = (base_dir / data_dir).resolve()
        if not media_dir.is_absolute():
            media_dir = (base_dir / media_dir).resolve()
        if type(limit) is not int or not 1024 <= limit <= 1024 * 1024 * 1024:
            raise ConfigError("storage.max_media_bytes 必须在 1 KiB–1 GiB 之间")
        return cls(data_dir=data_dir, media_dir=media_dir, max_media_bytes=limit)


@dataclass(frozen=True)
class WebUiConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 5099
    access_token: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WebUiConfig":
        enabled = value.get("enabled", True)
        host = value.get("host", "127.0.0.1")
        port = value.get("port", 5099)
        token = value.get("access_token", "")
        if type(enabled) is not bool:
            raise ConfigError("webui.enabled 必须是布尔值")
        if not isinstance(host, str) or not host.strip():
            raise ConfigError("webui.host 必须是非空字符串")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError("webui.port 必须是 1–65535 的整数")
        if not isinstance(token, str):
            raise ConfigError("webui.access_token 必须是字符串")
        return cls(enabled, host.strip(), port, token)


@dataclass(frozen=True)
class AppConfig:
    path: Path
    onebot: OneBotConfig
    reverse_ws: tuple[ReverseWsConfig, ...]
    driver: DriverConfig
    storage: StorageConfig
    webui: WebUiConfig = field(default_factory=WebUiConfig)
    app_name: str = APP_NAME
    version: str = __version__

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        target = ensure_config(path)
        try:
            raw = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"无法读取配置 {target}: {error}") from error
        if not isinstance(raw, dict):
            raise ConfigError("配置文件根节点必须是 JSON 对象")
        onebot_raw = raw.get("onebot", {})
        reverse_raw = raw.get("reverse_ws", [])
        driver_raw = raw.get("driver", {})
        storage_raw = raw.get("storage", {})
        webui_raw = raw.get("webui")
        if not isinstance(webui_raw, dict):
            webui_raw = {"enabled": True, "host": "127.0.0.1", "port": 5099,
                         "access_token": secrets.token_urlsafe(24)}
            raw["webui"] = webui_raw
        else:
            changed = False
            for key, value in (("enabled", True), ("host", "127.0.0.1"), ("port", 5099),
                               ("access_token", secrets.token_urlsafe(24))):
                if key not in webui_raw:
                    webui_raw[key] = value
                    changed = True
            if changed:
                raw["webui"] = webui_raw
        _atomic_write_json(target, raw)
        if not isinstance(onebot_raw, dict) or not isinstance(driver_raw, dict) or not isinstance(storage_raw, dict):
            raise ConfigError("onebot、driver、storage 必须是 JSON 对象")
        if not isinstance(reverse_raw, list):
            raise ConfigError("reverse_ws 必须是数组")
        base = target.parent
        return cls(
            path=target,
            onebot=OneBotConfig.from_dict(onebot_raw),
            reverse_ws=tuple(ReverseWsConfig.from_dict(item) for item in reverse_raw if isinstance(item, dict)),
            driver=DriverConfig.from_dict(driver_raw, base_dir=base),
            storage=StorageConfig.from_dict(storage_raw, base_dir=base),
            webui=WebUiConfig.from_dict(webui_raw),
        )


def update_account(path: str | Path, *, account: str, pid: int | None) -> None:
    target = Path(path).resolve()
    raw = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象")
    driver = raw.setdefault("driver", {})
    if not isinstance(driver, dict):
        raise ConfigError("driver 必须是 JSON 对象")
    driver["wechat_account"] = account
    driver["wechat_pid"] = pid
    _atomic_write_json(target, raw)
