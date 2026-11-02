from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

from ..config import AppConfig, account_directories
from ..driver import create_driver
from ..driver.base import Driver
from ..ids import IdMapper
from ..media import MediaStore
from ..onebot import MessageStore
from ..onebot.instance import OneBotInstance
from ..onebot.network import OneBotNetworkManager, load_or_create_account_network_config


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
    def create(cls, config: AppConfig, *, account: str | None = None, pid: int | None = None) -> "AccountRuntime":
        selected_account = account or config.driver.wechat_account or "unbound"
        selected_pid = config.driver.wechat_pid if pid is None else pid
        account_config = replace(
            config,
            driver=replace(config.driver, wechat_account=selected_account, wechat_pid=selected_pid),
        )
        account_dir, media_dir = account_directories(account_config, selected_account)
        account_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        mapper = IdMapper(account_dir / "ids.sqlite3")
        message_store = MessageStore(account_dir / "messages.sqlite3")
        network_config = load_or_create_account_network_config(account_dir / "onebot.json",
                                                               account_config, selected_account)
        base_url = _network_public_base_url(network_config, account_config)
        media = MediaStore(media_dir, max_bytes=config.storage.max_media_bytes, public_base_url=base_url)
        driver = create_driver(account_config)
        service = OneBotInstance(account_config, driver, mapper, media,
                                message_store=message_store, account=selected_account)
        network = OneBotNetworkManager(service, network_config)
        return cls(config=account_config, account=selected_account, pid=selected_pid,
                   account_dir=account_dir, media_dir=media_dir, mapper=mapper,
                   message_store=message_store, media=media, driver=driver, service=service,
                   network=network)

    async def start(self) -> None:
        if self.closed:
            raise RuntimeError("账号 runtime 已关闭")
        if not self.started:
            await self.service.start()
            try:
                await self.network.start()
            except Exception:
                await self.service.stop()
                raise
            self.started = True

    async def stop(self) -> None:
        if self.closed:
            return
        try:
            if self.started:
                await self.network.stop()
                await self.service.stop()
                self.started = False
        finally:
            self.mapper.close()
            self.message_store.close()
            self.closed = True

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
            self.last_error = "尚未绑定微信账号，请先运行 bind"
            return None
        try:
            runtime = await self.start_account(account, self.config.driver.wechat_pid)
            self.last_error = ""
            return runtime
        except Exception as error:
            self.last_error = str(error)
            return None

    async def dispatch(self, account: str, action: str, params: dict | None = None,
                       echo: object = None) -> dict:
        runtime = self.get(account)
        if runtime is None:
            from ..protocol import failure
            return failure(1404, f"账号 Runtime 不存在: {account}", echo)
        return await runtime.service.dispatch(action, params, echo)

    async def start_account(self, account: str, pid: int | None = None) -> AccountRuntime:
        async with self._lock:
            if account in self._runtimes:
                raise RuntimeError(f"账号 runtime 已存在: {account}")
            runtime = AccountRuntime.create(self.config, account=account, pid=pid)
            self._runtimes[account] = runtime
        try:
            await runtime.start()
        except Exception:
            async with self._lock:
                self._runtimes.pop(account, None)
            await runtime.stop()
            raise
        return runtime

    async def stop_account(self, account: str) -> None:
        async with self._lock:
            runtime = self._runtimes.pop(account, None)
        if runtime is not None:
            await runtime.stop()

    async def stop_all(self) -> None:
        accounts = list(self._runtimes)
        await asyncio.gather(*(self.stop_account(account) for account in accounts), return_exceptions=False)

    def get(self, account: str) -> AccountRuntime | None:
        return self._runtimes.get(account)

    def snapshot(self) -> list[dict[str, object]]:
        return [runtime.snapshot() for runtime in self._runtimes.values()]


def _network_public_base_url(network_config, config: AppConfig) -> str:
    servers = [row for row in network_config.http_servers if row.enabled]
    host = servers[0].host if servers else config.onebot.host
    port = servers[0].port if servers else config.onebot.port
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"
