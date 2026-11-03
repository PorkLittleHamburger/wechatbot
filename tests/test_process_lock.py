from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from wechat_onebot.bridge.process_lock import BridgeProcessLock, ProcessLockError
from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.driver.base import DriverError
from wechat_onebot.driver.helper import HelperDriver


def target_pid() -> int:
    # No live process is queried; randomized IDs isolate parallel offline tests.
    return 1_000_000 + uuid.uuid4().int % 1_000_000_000


CONTENDER = """
import sys
from wechat_onebot.bridge.process_lock import BridgeProcessLock, ProcessLockError
guard = BridgeProcessLock(int(sys.argv[1]))
try:
    guard.acquire()
except ProcessLockError:
    sys.exit(23)
if len(sys.argv) > 2:
    print('READY', flush=True)
    sys.stdin.readline()
guard.release()
"""

# Windows venv launchers may spawn the real interpreter as a child. Use the
# interpreter directly so kill()/wait() refer to the process holding the lock.
CHILD_PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def child_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / "src"), env.get("PYTHONPATH", "")]
    )
    return env


def contend(pid: int) -> int:
    result = subprocess.run(
        [CHILD_PYTHON, "-c", CONTENDER, str(pid)], env=child_env(),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode in (0, 23), result.stderr
    return result.returncode


@pytest.mark.parametrize("pid", [None, True, False, 0, -1, "123", 1.5])
def test_requires_explicit_positive_pid(pid):
    with pytest.raises(ValueError, match="PID"):
        BridgeProcessLock(pid)


def test_same_process_and_subprocess_exclusion_and_release():
    pid = target_pid()
    owner = BridgeProcessLock(pid)
    other = BridgeProcessLock(pid)
    distinct = BridgeProcessLock(pid + 1)
    owner.acquire()
    try:
        with pytest.raises(ProcessLockError):
            owner.acquire()
        with pytest.raises(ProcessLockError):
            other.acquire()
        other.release()  # A losing contender must not release the owner's lock.
        assert contend(pid) == 23
        distinct.acquire()
        distinct.release()
        assert contend(pid + 1) == 0
    finally:
        distinct.release()
        owner.release()
    assert owner.path.exists()
    owner.release()
    assert contend(pid) == 0
    other.acquire()
    other.release()
    assert owner.path.exists()  # Never unlink the rendezvous point.


def test_abrupt_owner_exit_releases_os_lock():
    pid = target_pid()
    child = subprocess.Popen(
        [CHILD_PYTHON, "-c", CONTENDER, str(pid), "hold"],
        env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    ready = threading.Event()
    lines = []

    def read_ready():
        lines.append(child.stdout.readline())
        ready.set()

    reader = threading.Thread(target=read_ready, daemon=True)
    reader.start()
    try:
        assert ready.wait(15), "child did not acquire lock"
        assert lines == ["READY\n"]
        assert contend(pid) == 23
        guard = BridgeProcessLock(pid)
        with pytest.raises(ProcessLockError):
            guard.acquire()
        child.kill()
        child.wait(timeout=15)
        guard.acquire()
        guard.release()
        assert guard.path.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)
        reader.join(timeout=2)


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    # Import substitutions prevent all native module loading/injection/sending.
    root = tmp_path / "helper"
    (root / "native").mkdir(parents=True)
    config = AppConfig(
        path=tmp_path / "config.json", onebot=OneBotConfig(access_token="test"),
        reverse_ws=(), storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024),
        driver=DriverConfig(
            type="helper", helper_root=root, wechat_account="wxid_offline",
            wechat_pid=target_pid(), wechat_data_directory=tmp_path,
        ),
    )
    state = SimpleNamespace(
        backends=[], fail_start=False, fail_cleanup=False, fail_bind=False,
        fail_facade=False, on_cleanup=lambda: None, on_construct=lambda: None,
        block_start=False, entered=threading.Event(), resume=threading.Event(),
    )

    class Backend:
        def __init__(self, *_args, **_kwargs):
            state.on_construct()
            self.raw = self
            self.thread = None
            state.backends.append(self)

        def register_msg_handler(self, _handler):
            pass

        def start(self):
            self.thread = threading.get_ident()
            if state.block_start:
                state.entered.set()
                assert state.resume.wait(15)
            if state.fail_start:
                raise RuntimeError("start failed")

        def stop(self):
            if self.thread is not None:
                assert threading.get_ident() == self.thread
            state.on_cleanup()
            if state.fail_cleanup:
                raise RuntimeError("cleanup failed")

    class Facade:
        def __init__(self, **_kwargs):
            if state.fail_facade:
                raise RuntimeError("facade failed")

        def bind(self, backend):
            if state.fail_bind:
                raise RuntimeError("bind failed")
            self.backend = backend

        def close(self):
            self.backend.stop()

    package = ModuleType("wxbot_framework")
    backend = ModuleType("wxbot_framework.backend")
    sdk = ModuleType("wxbot_framework.sdk")
    backend.NativeTextBackend = Backend
    sdk.ServiceFacade = Facade
    monkeypatch.setitem(sys.modules, "wxbot_framework", package)
    monkeypatch.setitem(sys.modules, "wxbot_framework.backend", backend)
    monkeypatch.setitem(sys.modules, "wxbot_framework.sdk", sdk)
    # The driver is Windows-only; the fake backend also permits POSIX CI.
    monkeypatch.setattr(sys, "platform", "win32")
    return config, state


async def emit(_event):
    pass


async def test_driver_exclusion_until_full_cleanup(fake_helper):
    config, state = fake_helper
    pid = config.driver.wechat_pid
    first, second = HelperDriver(config), HelperDriver(config)
    state.on_construct = lambda: assert_contended(pid)
    state.on_cleanup = lambda: assert_contended(pid)
    try:
        await first.start(emit)
        with pytest.raises(DriverError, match="桥接锁"):
            await second.start(emit)
        assert len(state.backends) == 1
        with pytest.raises(DriverError):
            first.rebind(config)
        with pytest.raises(DriverError):
            await first.start(emit)
        await first.stop()
        await second.start(emit)
        await second.stop()
        assert contend(pid) == 0
    finally:
        await first.stop()
        await second.stop()


def assert_contended(pid):
    assert contend(pid) == 23


@pytest.mark.parametrize("failure", ["fail_start", "fail_bind", "fail_facade"])
async def test_failed_start_releases_only_after_successful_cleanup(fake_helper, failure):
    config, state = fake_helper
    setattr(state, failure, True)
    driver = HelperDriver(config)
    state.on_cleanup = lambda: assert_contended(config.driver.wechat_pid)
    with pytest.raises(DriverError, match="启动失败"):
        await driver.start(emit)
    assert driver._backend is None
    assert driver._process_lock is None
    assert contend(config.driver.wechat_pid) == 0


@pytest.mark.parametrize("startup_failure", [False, True])
async def test_cleanup_failure_retains_guard_until_retry(fake_helper, startup_failure):
    config, state = fake_helper
    driver = HelperDriver(config)
    try:
        if startup_failure:
            state.fail_start = state.fail_cleanup = True
            with pytest.raises(DriverError, match="清理失败"):
                await driver.start(emit)
        else:
            await driver.start(emit)
            state.fail_cleanup = True
            with pytest.raises(RuntimeError, match="cleanup failed"):
                await driver.stop()
        assert driver._process_lock.held
        assert driver._service is not None
        assert contend(config.driver.wechat_pid) == 23
        with pytest.raises(DriverError):
            driver.rebind(replace(config, driver=replace(config.driver, wechat_pid=target_pid())))
        with pytest.raises(DriverError, match="桥接锁"):
            await HelperDriver(config).start(emit)
        state.fail_start = state.fail_cleanup = False
        await driver.start(emit)  # Retry cleanup, then reacquire for a fresh start.
        await driver.stop()
        assert contend(config.driver.wechat_pid) == 0
    finally:
        state.fail_cleanup = False
        await driver.stop()


async def test_cancellation_retains_lock_until_worker_and_cleanup_finish(fake_helper):
    config, state = fake_helper
    state.block_start = True
    driver = HelperDriver(config)
    task = asyncio.create_task(driver.start(emit))
    try:
        assert await asyncio.to_thread(state.entered.wait, 15)
        with pytest.raises(DriverError, match="正在启动或清理"):
            await driver.start(emit)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert contend(config.driver.wechat_pid) == 23
    finally:
        state.resume.set()
        await driver.stop()
    assert contend(config.driver.wechat_pid) == 0
