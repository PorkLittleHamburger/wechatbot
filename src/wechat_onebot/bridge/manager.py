from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Callable, Protocol

from ..config import AppConfig
from ..driver import create_driver
from .base import BridgeSession, EventCallback
from .session import MockBridgeSession


class BridgeSessionListener(Protocol):
    def started(self, account: str, session: BridgeSession) -> None: ...
    def closed(self, account: str, session: BridgeSession, pid: int) -> None: ...


SessionFactory = Callable[[AppConfig, str, int | None], BridgeSession]


@dataclass(frozen=True)
class BridgeRecord:
    account: str
    pid: int | None
    session: BridgeSession


class BridgeManager:
    """Owns account-level bridge sessions and enforces account/PID isolation."""

    def __init__(self, config: AppConfig, *, session_factory: SessionFactory | None = None):
        self.config = config
        self._sessions: dict[str, BridgeRecord] = {}
        self._pid_to_account: dict[int, str] = {}
        self._listeners: list[BridgeSessionListener] = []
        self._lock = asyncio.Lock()
        self._session_factory = session_factory or self._default_factory

    def _default_factory(self, config: AppConfig, account: str, pid: int | None) -> BridgeSession:
        account_config = replace(
            config,
            driver=replace(config.driver, wechat_account=account, wechat_pid=pid),
        )
        if config.driver.type == "mock":
            return MockBridgeSession()
        return create_driver(account_config)

    def subscribe(self, listener: BridgeSessionListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _fire_started(self, account: str, session: BridgeSession) -> None:
        for listener in tuple(self._listeners):
            try:
                listener.started(account, session)
            except Exception:
                import logging
                logging.getLogger("wechat-onebot.bridge").exception("bridge started listener failed")

    def _fire_closed(self, account: str, session: BridgeSession, pid: int) -> None:
        for listener in tuple(self._listeners):
            try:
                listener.closed(account, session, pid)
            except Exception:
                import logging
                logging.getLogger("wechat-onebot.bridge").exception("bridge closed listener failed")

    async def start_session(self, account: str, pid: int | None, emit: EventCallback) -> BridgeRecord:
        if not account:
            raise ValueError("account 不能为空")
        async with self._lock:
            if account in self._sessions:
                raise RuntimeError(f"账号已存在 Bridge session: {account}")
            if pid is not None and pid in self._pid_to_account:
                raise RuntimeError(f"PID 已绑定其他账号: {pid} -> {self._pid_to_account[pid]}")
            session = self._session_factory(self.config, account, pid)
            record = BridgeRecord(account, pid, session)
            self._sessions[account] = record
            if pid is not None:
                self._pid_to_account[pid] = account
            try:
                await session.start(emit)
            except Exception:
                self._sessions.pop(account, None)
                if pid is not None:
                    self._pid_to_account.pop(pid, None)
                raise
            self._fire_started(account, session)
        return record

    async def stop_session(self, account: str) -> None:
        async with self._lock:
            record = self._sessions.get(account)
            if record is None:
                return
            await record.session.stop()
            self._sessions.pop(account)
            if record.pid is not None:
                self._pid_to_account.pop(record.pid, None)
                self._fire_closed(account, record.session, record.pid)

    async def stop_all(self) -> None:
        accounts = list(self._sessions)
        await asyncio.gather(*(self.stop_session(account) for account in accounts), return_exceptions=False)

    def get(self, account: str) -> BridgeRecord | None:
        return self._sessions.get(account)

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "account": record.account,
                "pid": record.pid,
                "online": record.session.online,
                "capabilities": sorted(record.session.capabilities),
            }
            for record in self._sessions.values()
        ]
