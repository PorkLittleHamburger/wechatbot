"""Single owner for startup recovery and explicit bridge lifecycle commands."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from ..bridge import discover_logged_accounts, discover_sdk_accounts
from ..config import AppConfig
from ..core.runtime import AccountNotLoggedInError, RuntimeManager
from .startup import load_bridge_intent, save_bridge_intent, save_last_bridge


RESUME_CAPABILITY_VERSION = 1

# A helper-configured instance may target a 4.2.0.52 client that only the SDK
# driver can reach. Probe the SDK after a short grace period, then sparingly:
# the resume loop runs every few milliseconds and must not scan processes on
# every iteration.
SDK_PROBE_DELAY = 2.0
SDK_PROBE_INTERVAL = 5.0


class BridgeResumeController:
    def __init__(self, config: AppConfig, manager: RuntimeManager, *, enabled: bool = False,
                 check_seconds: float = 1.0, discover: Callable = discover_logged_accounts):
        self.config = config
        self.manager = manager
        self.enabled = enabled
        self.check_seconds = check_seconds
        self.discover = discover
        self.account: str | None = None
        self.desired = "stopped"
        self.state = "disabled"
        self.error = ""
        self._generation = 0
        self._wake = asyncio.Event()
        self._commands = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._owned: set[asyncio.Task] = set()
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self._waiting_since: float | None = None
        self._sdk_checked_at = 0.0

    def snapshot(self) -> dict:
        return {"supported": True, "version": RESUME_CAPABILITY_VERSION,
                "enabled": self.enabled, "account": self.account, "desired": self.desired,
                "state": self.state, "error": self.error}

    async def initialize(self) -> None:
        try:
            self.account, self.desired = load_bridge_intent(self.config.storage.data_dir)
        except (OSError, ValueError) as error:
            self._fail(error)
            return
        if self.enabled:
            await self.resume()

    async def resume(self) -> dict:
        """Explicit, idempotent request; a saved manual stop remains authoritative."""
        if self._closing:
            raise RuntimeError("桥接管理服务正在关闭")
        self.enabled = True
        if self.state in ("starting", "stopping") and self._owned:
            return self.snapshot()
        if self._task is not None and not self._task.done():
            return self.snapshot()
        if not self.account:
            self.state = "no_target"
        elif self.desired == "stopped":
            self.state = "stopped"
        else:
            self._generation += 1
            if self.error and self.manager.last_error == self.error:
                self.manager.last_error = ""
            self.error = ""
            self._waiting_since = None
            self._sdk_checked_at = 0.0
            self.state = "waiting_login"
            self._task = asyncio.create_task(self._run(self.account, self._generation))
        return self.snapshot()

    def _invalidate(self) -> int:
        self._generation += 1
        self._wake.set()
        return self._generation

    def _current(self, generation: int) -> bool:
        return not self._closing and generation == self._generation

    def _fail(self, error: Exception) -> None:
        self.state, self.error = "failed", str(error)
        self.manager.last_error = self.error

    async def _pause(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), self.check_seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def _run(self, account: str, generation: int) -> None:
        while self._current(generation):
            current = self.manager.get(account)
            if current is not None and current.started:
                self.state = "running"
                return
            if self.config.driver.type in ("helper", "sdk"):
                try:
                    if self.config.driver.type == "sdk":
                        rows = await asyncio.to_thread(discover_sdk_accounts, self.config)
                    else:
                        rows = await asyncio.to_thread(self.discover, self.config.driver.helper_root)
                except Exception as error:
                    if self._current(generation):
                        self._fail(error)
                    return
                if not self._current(generation):
                    return
                matches = [row for row in rows if row.wxid == account]
                if matches:
                    self._waiting_since = None
                elif self.config.driver.type == "helper":
                    # Existing helper configs upgraded to 0.10+ may target a
                    # 4.2.0.52 client that only the SDK driver can reach. Probe
                    # after a grace period instead of scanning every loop.
                    now = time.monotonic()
                    if self._waiting_since is None:
                        self._waiting_since = now
                    if (now - self._waiting_since >= SDK_PROBE_DELAY
                            and now - self._sdk_checked_at >= SDK_PROBE_INTERVAL):
                        self._sdk_checked_at = now
                        try:
                            sdk_rows = await asyncio.to_thread(discover_sdk_accounts, self.config)
                        except Exception:
                            sdk_rows = []
                        matches = [row for row in sdk_rows if row.wxid == account]
                if len(matches) > 1:
                    self._fail(RuntimeError("目标账号无法唯一识别，自动恢复已停止"))
                    return
                if not matches:
                    self.state = "waiting_login"
                    await self._pause()
                    continue
            if not self._current(generation):
                return
            self.state = "starting"
            try:
                # Never cancel this await: native initialization may be in a thread.
                await self.manager.start_account(account)
            except AccountNotLoggedInError:
                if self._current(generation):
                    self.state = "waiting_login"
                    await self._pause()
                continue
            except Exception as error:
                if self._current(generation):
                    self._fail(error)
                return
            if self._current(generation):
                self.state, self.error = "running", ""
                self.manager.last_error = ""
            return

    async def _drain_resume(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _own(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._owned.add(task)
        def done(completed):
            self._owned.discard(completed)
            if not completed.cancelled():
                completed.exception()  # Consume failures when an HTTP waiter disconnects.
        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def start_account(self, account: str, pid: int | None = None) -> str:
        if self._closing:
            raise RuntimeError("桥接管理服务正在关闭")
        generation = self._invalidate()
        self.account, self.desired = account, "running"
        self.state, self.error = "starting", ""
        return await self._own(self._start_account(account, pid, generation))

    async def _start_account(self, account: str, pid: int | None, generation: int) -> str:
        async with self._commands:
            await self._drain_resume()
            if not self._current(generation):
                raise RuntimeError("启动请求已被后续操作撤销")
            self.account, self.desired = account, "running"
            self.state, self.error = "starting", ""
            try:
                current = self.manager.get(account)
                if current is None or not current.started:
                    await self.manager.start_account(account, pid)
            except Exception as error:
                if self._current(generation):
                    self._fail(error)
                raise
            if not self._current(generation):
                raise RuntimeError("启动请求已被后续操作撤销")
            self.state = "running"
            try:
                save_last_bridge(self.config.storage.data_dir, account)
            except OSError:
                self.error = "桥接已启动，但无法保存下次自启账号"
            self.manager.last_error = self.error
            return self.error

    async def stop_account(self, account: str) -> str:
        if self._closing:
            raise RuntimeError("桥接管理服务正在关闭")
        cancels_resume = self.account == account
        generation = self._invalidate() if cancels_resume else self._generation
        warning = ""
        if cancels_resume:
            self.desired, self.state = "stopped", "stopping"
        try:
            saved_account, _ = load_bridge_intent(self.config.storage.data_dir)
        except (OSError, ValueError):
            saved_account = None
            if not cancels_resume:
                warning = "桥接停止后无法核对持久化恢复目标，请检查 startup.json 与数据目录权限"
        if cancels_resume or saved_account == account:
            try:
                save_bridge_intent(self.config.storage.data_dir, account, "stopped")
            except OSError:
                warning = "本次恢复已取消，但无法保存停止意图，下次启动前请检查数据目录权限"
        return await self._own(self._stop_account(account, generation, cancels_resume, warning))

    async def _stop_account(self, account: str, generation: int, cancels_resume: bool, warning: str) -> str:
        async with self._commands:
            if cancels_resume:
                await self._drain_resume()
            try:
                await self.manager.stop_account(account)
            except Exception as error:
                if generation == self._generation and self.account == account:
                    self._fail(error)
                raise
            if generation == self._generation and self.account == account:
                self.state, self.error = "stopped", warning
                self.manager.last_error = warning
        return warning

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._invalidate()
            self._close_task = asyncio.create_task(self._drain_lifecycle())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._close_task)
                break
            except asyncio.CancelledError:
                if self._close_task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _drain_lifecycle(self) -> None:
        try:
            await self._drain_resume()
        finally:
            await asyncio.gather(*self._owned, return_exceptions=True)
