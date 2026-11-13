from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from ..config import AppConfig, account_directories, update_account
from ..bridge import discover_logged_accounts, discover_sdk_accounts, sdk_discovery_report
from ..driver import create_driver
from ..driver.base import Driver
from ..ids import IdMapper
from ..media import MediaStore
from ..onebot import MessageStore
from ..onebot.instance import OneBotInstance
from ..onebot.network import (AccountNetworkConfig, OneBotNetworkManager,
                              load_or_create_account_network_config, save_account_network_config)

logger = logging.getLogger("wechat-onebot.runtime")


class AccountNotLoggedInError(RuntimeError):
    """The selected account disappeared before native startup began."""


class AccountAlreadyStartedError(RuntimeError):
    """The account runtime is already running."""


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

    async def _ensure_network_locked(self, account: str, network_config: AccountNetworkConfig | None = None,
                                     *, driver_type: str | None = None) -> AccountRuntime:
        runtime = self._runtimes.get(account)
        if runtime is not None:
            return runtime
        runtime_config = self.config
        if driver_type and driver_type != self.config.driver.type:
            runtime_config = replace(self.config, driver=replace(self.config.driver, type=driver_type))
        runtime = AccountRuntime.create(runtime_config, account=account, network_config=network_config)
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

    async def _discover_for(self, driver_type: str) -> list:
        if driver_type == "sdk":
            return await asyncio.to_thread(discover_sdk_accounts, self.config)
        return await asyncio.to_thread(discover_logged_accounts, self.config.driver.helper_root)

    async def _resolve_account(self, account: str) -> tuple[str | None, object | None]:
        """Pick the configured driver, falling back to the other real driver.

        A gateway upgraded from helper to 0.10.0 keeps ``driver.type=helper`` in
        its existing config while the installed WeChat is a 4.2.0.52 build that
        only the SDK driver supports. Trying the other driver here removes that
        migration trap instead of reporting the account as logged out.
        """
        configured = self.config.driver.type
        if configured == "mock":
            return None, None
        order: list[str] = []
        if configured in ("helper", "sdk"):
            order.append(configured)
        for kind in ("helper", "sdk"):
            if kind not in order:
                order.append(kind)
        for kind in order:
            try:
                rows = await self._discover_for(kind)
            except Exception as error:
                logger.info("%s 驱动账号发现失败: %s", kind, error)
                continue
            matches = [row for row in rows if row.wxid == account]
            if len(matches) == 1:
                if kind != configured:
                    logger.info("配置驱动 %s 未找到账号 %s，改用 %s 驱动", configured, account, kind)
                return kind, matches[0]
            if len(matches) > 1:
                raise RuntimeError("目标账号无法唯一识别，请在 WebUI 重新扫描")
        try:
            _, diagnostics = await asyncio.to_thread(sdk_discovery_report, self.config)
        except Exception:
            diagnostics = []
        versions = sorted({row["version"] for row in diagnostics if row.get("version")})
        codes = sorted({row["error"] for row in diagnostics
                        if row.get("error") and row["error"] != "NOT_LOGGED_IN"})
        hint = ""
        if versions:
            hint += f" 检测到微信构建 {', '.join(versions)}。"
        if codes:
            hint += f" 发现错误码 {', '.join(codes)}。"
        raise AccountNotLoggedInError(
            f"目标账号未登录，或当前微信构建不受支持（已尝试 {'/'.join(order)} 驱动）。{hint}"
            "4.2.0.52 需要 sdk 驱动，4.1.15.x / 4.2.0.11 / 4.2.0.12 需要 helper 驱动；"
            "请确认微信已登录、账号一致，且没有旧桥接占用该进程。"
        )

    def _remember_driver(self, driver_type: str | None, account: str, pid: int | None,
                         login: object | None) -> None:
        """Persist account/binding facts, never the configured driver type.

        The Launcher owns the instance mode and validates
        ``config.driver.type == instance.mode`` before every start. Rewriting the
        type from a runtime fallback would break that contract (and backups), so
        an automatic switch stays in-memory only.
        """
        if driver_type is None:
            return
        nickname = str(getattr(login, "nickname", "") or "") or self.config.driver.nickname
        bound = account == self.config.driver.wechat_account or not self.config.driver.wechat_account
        changed = bound and (pid != self.config.driver.wechat_pid
                             or nickname != self.config.driver.nickname)
        if not changed:
            return
        try:
            update_account(self.config.path, account=account, pid=pid, nickname=nickname)
        except Exception:
            logger.warning("自动回退后写入账号绑定失败", exc_info=True)
        self.config = replace(self.config, driver=replace(
            self.config.driver, wechat_account=account, wechat_pid=pid, nickname=nickname))

    async def _start_with_driver(self, account: str, driver_type: str,
                                 login: object | None, pid: int | None) -> AccountRuntime:
        target_pid = login.pid if login is not None else pid
        async with self._lock:
            runtime = await self._ensure_network_locked(account, driver_type=driver_type)
            if runtime.started:
                raise AccountAlreadyStartedError(f"账号 runtime 已启动: {account}")
            try:
                runtime.set_pid(target_pid)
                await runtime.start()
            except Exception:
                await self._discard_runtime(account, runtime)
                raise
        return runtime

    async def _discard_runtime(self, account: str, runtime: AccountRuntime) -> None:
        if self._runtimes.get(account) is runtime:
            self._runtimes.pop(account, None)
        try:
            await runtime.close()
        except Exception:
            logger.warning("清理启动失败的账号 runtime 时出错", exc_info=True)

    async def _find_account(self, driver_type: str, account: str):
        rows = await self._discover_for(driver_type)
        matches = [row for row in rows if row.wxid == account]
        if len(matches) > 1:
            raise RuntimeError("目标账号无法唯一识别，请在 WebUI 重新扫描")
        return matches[0] if matches else None

    async def start_account(self, account: str, pid: int | None = None) -> AccountRuntime:
        driver_type, login = await self._resolve_account(account)
        try:
            runtime = await self._start_with_driver(account, driver_type, login, pid)
        except Exception as error:
            if driver_type not in ("helper", "sdk") or isinstance(error, AccountAlreadyStartedError):
                raise
            # A driver can verify the account in its discovery pass yet still
            # reject the native start (unsupported build, stale PID, account
            # mismatch). Retry once with the other real driver when it can see
            # the same account; persist only the driver that actually starts.
            other = "sdk" if driver_type == "helper" else "helper"
            try:
                alternate = await self._find_account(other, account)
            except Exception:
                alternate = None
            if alternate is None:
                raise
            logger.warning("驱动 %s 启动失败（%s），改用 %s 重试", driver_type, error, other)
            runtime = await self._start_with_driver(account, other, alternate, pid)
            driver_type, login = other, alternate
        final_pid = getattr(runtime, "pid", None)
        if final_pid is None:
            final_pid = login.pid if login is not None else pid
        self._remember_driver(driver_type, account, final_pid, login)
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
