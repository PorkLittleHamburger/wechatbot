from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

from ..config import AppConfig, account_directories
from ..bridge import discover_logged_accounts, discover_sdk_accounts
from ..driver import create_driver
from ..driver.base import Driver
from ..ids import IdMapper
from ..media import MediaStore
from ..onebot import MessageStore
from ..onebot.instance import OneBotInstance
from ..onebot.network import (AccountNetworkConfig, OneBotNetworkManager,
                              load_or_create_account_network_config, save_account_network_config)


class AccountNotLoggedInError(RuntimeError):
    """The selected account disappeared before native startup began."""


@dataclass
class AccountRuntime:
    config: AppConfig
    account: str
    pid: int | None
    account_dir: Path
    media_dir: Path
    mapper: IdMapper
    message_store: MessageStore
    media: MediaStore
    driver: Driver
    service: OneBotInstance
    network: OneBotNetworkManager
    started: bool = False
    closed: bool = False

    @classmethod
    def create(cls, config: AppConfig, *, account: str | None = None, pid: int | None = None,
               network_config: AccountNetworkConfig | None = None) -> "AccountRuntime":
        selected_account = account or config.driver.wechat_account or "unbound"
        selected_pid = (config.driver.wechat_pid if selected_account == config.driver.wechat_account else None) if pid is None else pid
        account_config = replace(
            config,
            driver=replace(config.driver, wechat_account=selected_account, wechat_pid=selected_pid),
        )
        account_dir, media_dir = account_directories(account_config, selected_account)
        account_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        mapper = IdMapper(account_dir / "ids.sqlite3")
        message_store = MessageStore(account_dir / "messages.sqlite3")
        if network_config is None:
            network_config = load_or_create_account_network_config(account_dir / "onebot.json",
                                                                   account_config, selected_account)
        base_url = _network_public_base_url(network_config, account_config, selected_account)
        media = MediaStore(media_dir, max_bytes=config.storage.max_media_bytes, public_base_url=base_url)
        driver = create_driver(account_config)
        service = OneBotInstance(account_config, driver, mapper, media,
                                message_store=message_store, account=selected_account)
        service.self_id = mapper.get_or_create("user", selected_account)
        service.group_mention_only = network_config.group_mention_only
        network = OneBotNetworkManager(service, network_config)
        return cls(config=account_config, account=selected_account, pid=selected_pid,
                   account_dir=account_dir, media_dir=media_dir, mapper=mapper,
                   message_store=message_store, media=media, driver=driver, service=service,
                   network=network)

    async def start(self) -> None:
        if self.closed:
            raise RuntimeError("账号 runtime 已关闭")
        if not self.started:
            await self.network.start()
            await self.service.start()
            self.started = True

    async def stop(self) -> None:
        if self.closed:
            return
        if self.started:
            try:
                await self.service.stop()
            finally:
                self.started = False

    async def close(self) -> None:
        if self.closed:
            return
        try:
            try:
                await self.stop()
            finally:
                await self.network.stop()
        finally:
            self.mapper.close()
            self.message_store.close()
            self.closed = True

    def set_pid(self, pid: int | None) -> None:
        if self.started:
            raise RuntimeError("运行中的账号不能更改 PID")
        self.pid = pid
        self.config = replace(self.config, driver=replace(self.config.driver, wechat_pid=pid))
        self.service.config = self.config
        rebind = getattr(self.driver, "rebind", None)
        if rebind is not None:
            rebind(self.config)

    async def reconfigure_network(self, config: AccountNetworkConfig) -> None:
        if self.closed:
            raise RuntimeError("账号 Runtime 已关闭")
        await self.network.reconfigure(config)
        self.service.group_mention_only = config.group_mention_only
        self.media.public_base_url = _network_public_base_url(config, self.config, self.account)

    def snapshot(self) -> dict[str, object]:
        return {
            "account": self.account,
            "pid": self.pid,
            "online": self.driver.online,
            "started": self.started,
            "data_dir": str(self.account_dir),
            "media_dir": str(self.media_dir),
            "self_id": self.service.self_id,
            "network": self.network.snapshot(),
            "message_count": self.message_store.stats()["message_count"],
        }


class RuntimeManager:
    """Owns isolated account runtimes and their lifecycle."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._runtimes: dict[str, AccountRuntime] = {}
        self._lock = asyncio.Lock()
        self.last_error = ""

    async def start_configured_account(self) -> AccountRuntime | None:
        account = self.config.driver.wechat_account
        if not account:
            self.last_error = "尚未选择微信账号，请在 WebUI 的进程与账号页面扫描并选择"
            return None
        try:
            runtime = await self.start_account(account, self.config.driver.wechat_pid)
            self.last_error = ""
            return runtime
        except Exception as error:
            self.last_error = str(error)
            return None

    async def start_saved_networks(self) -> None:
        account = self.config.driver.wechat_account
        if not account:
            return
        account_dir, _ = account_directories(self.config, account)
        network_config = load_or_create_account_network_config(account_dir / "onebot.json", self.config, account)
        if any(row.enabled for rows in (network_config.http_servers, network_config.http_clients,
                                        network_config.ws_servers, network_config.ws_clients) for row in rows):
            await self.ensure_network(account, network_config)

    async def ensure_network(self, account: str, network_config: AccountNetworkConfig | None = None) -> AccountRuntime:
        async with self._lock:
            return await self._ensure_network_locked(account, network_config)

    async def _ensure_network_locked(self, account: str, network_config: AccountNetworkConfig | None = None) -> AccountRuntime:
        runtime = self._runtimes.get(account)
        if runtime is not None:
            return runtime
        runtime = AccountRuntime.create(self.config, account=account, network_config=network_config)
        try:
            await runtime.network.start()
        except Exception:
            await runtime.close()
            raise
        self._runtimes[account] = runtime
        return runtime

    async def apply_network_config(self, account: str, network_config: AccountNetworkConfig) -> bool:
        async with self._lock:
            account_dir, _ = account_directories(self.config, account)
            path = account_dir / "onebot.json"
            runtime = self._runtimes.get(account)
            if runtime is None:
                enabled = any(row.enabled for rows in (network_config.http_servers, network_config.http_clients,
                              network_config.ws_servers, network_config.ws_clients) for row in rows)
                if not enabled:
                    save_account_network_config(path, network_config)
                    return False
                runtime = AccountRuntime.create(self.config, account=account, network_config=network_config)
                try:
                    await runtime.network.start()
                    save_account_network_config(path, network_config)
                except Exception:
                    await runtime.close()
                    raise
                self._runtimes[account] = runtime
                return True
            previous = runtime.network.config
            await runtime.reconfigure_network(network_config)
            try:
                save_account_network_config(path, network_config)
            except Exception:
                await runtime.reconfigure_network(previous)
                raise
            return runtime.network.started

    async def dispatch(self, account: str, action: str, params: dict | None = None,
                       echo: object = None) -> dict:
        runtime = self.get(account)
        if runtime is None:
            from ..protocol import failure
            return failure(1404, f"账号 Runtime 不存在: {account}", echo)
        return await runtime.service.dispatch(action, params, echo)

    async def start_account(self, account: str, pid: int | None = None) -> AccountRuntime:
        if self.config.driver.type == "helper":
            discovered = await asyncio.to_thread(discover_logged_accounts, self.config.driver.helper_root)
            matches = [row for row in discovered if row.wxid == account]
            if not matches:
                raise AccountNotLoggedInError("目标账号未登录，请在 PC 微信中登录")
            if len(matches) != 1:
                raise RuntimeError("目标账号无法唯一识别，请在 WebUI 重新扫描")
            pid = matches[0].pid
        elif self.config.driver.type == "sdk":
            discovered = await asyncio.to_thread(discover_sdk_accounts, self.config)
            matches = [row for row in discovered if row.wxid == account]
            if not matches:
                raise AccountNotLoggedInError(
                    "目标账号未登录，或当前微信构建不在 wechatbot_api 已适配范围内"
                )
            if len(matches) != 1:
                raise RuntimeError("目标账号无法唯一识别，请在 WebUI 重新扫描")
            pid = matches[0].pid
        async with self._lock:
            runtime = await self._ensure_network_locked(account)
            if runtime.started:
                raise RuntimeError(f"账号 runtime 已启动: {account}")
            runtime.set_pid(pid)
            await runtime.start()
        return runtime

    async def stop_account(self, account: str) -> None:
        async with self._lock:
            runtime = self._runtimes.get(account)
            if runtime is not None:
                await runtime.stop()

    async def stop_all(self) -> None:
        async with self._lock:
            results = await asyncio.gather(*(runtime.close() for runtime in self._runtimes.values()),
                                           return_exceptions=True)
            self._runtimes.clear()
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    def get(self, account: str) -> AccountRuntime | None:
        return self._runtimes.get(account)

    def snapshot(self) -> list[dict[str, object]]:
        return [runtime.snapshot() for runtime in self._runtimes.values()]


def account_media_key(account: str) -> str:
    return hashlib.sha256(account.encode("utf-8")).hexdigest()


def _network_public_base_url(network_config, config: AppConfig, account: str) -> str:
    servers = [row for row in network_config.http_servers if row.enabled]
    # The main HTTP application serves account-scoped media even when OneBot
    # communication is WS-only. Never advertise the legacy, unbound API port.
    host = servers[0].host if servers else config.webui.host
    port = servers[0].port if servers else config.webui.port
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    prefix = "" if servers else f"/accounts/{account_media_key(account)}"
    return f"http://{host}:{port}{prefix}"
