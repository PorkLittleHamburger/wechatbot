from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest


HELPER = Path(__file__).resolve().parents[1] / "vendor" / "helper"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows bridge watchdog")


def test_vendored_helper_hashes_match():
    manifest = json.loads((HELPER / "VENDORED_SHA256.json").read_text(encoding="utf-8"))
    for item in manifest:
        path = HELPER.joinpath(*item["RelativePath"].split("\\"))
        content = path.read_bytes()
        assert len(content) == item["Length"], item["RelativePath"]
        assert hashlib.sha256(content).hexdigest().upper() == item["SHA256"], item["RelativePath"]


@pytest.fixture
def lifecycle(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    from wxbot_framework import bridge_lifecycle
    return bridge_lifecycle


def test_watchdog_help_with_isolated_python(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8",
         str(HELPER / "native" / "bridge_watchdog.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert "--ready-event" in result.stdout


def test_watchdog_early_exit_preserves_stderr_and_exit_code(tmp_path, lifecycle):
    native = tmp_path / "helper with spaces" / "native"
    native.mkdir(parents=True)
    script = native / "watchdog.py"
    script.write_text("import sys\nprint('startup import failed', file=sys.stderr)\n"
                      "raise SystemExit(7)\n", encoding="utf-8")
    startup_log = native.parent / "logs" / "bridge_watchdog_startup.log"
    guard = lifecycle.BridgeGuard(os.getpid())
    try:
        with pytest.raises(RuntimeError, match="提前退出（退出码 7）") as error:
            guard.start_watchdog(script)
        assert str(startup_log) in str(error.value)
        assert "startup import failed" in startup_log.read_text(encoding="utf-8")
        assert "退出码 7" in startup_log.read_text(encoding="utf-8")
        assert not (startup_log.parent / "bridge_watchdog.log").exists()
    finally:
        guard.close()


def test_watchdog_ready_and_normal_stop_without_cleanup(tmp_path, lifecycle):
    native = tmp_path / "helper with spaces" / "native"
    native.mkdir(parents=True)
    script = native / "watchdog.py"
    script.write_text(
        "import argparse, sys\n"
        f"sys.path.insert(0, {str(HELPER)!r})\n"
        "from wxbot_framework.bridge_lifecycle import watch_owner\n"
        "p = argparse.ArgumentParser()\n"
        "for name in ('owner-pid', 'wechat-pid', 'stop-event', 'ready-event'):\n"
        "    p.add_argument('--' + name, required=True)\n"
        "a = p.parse_args()\n"
        "def cleanup(pid):\n"
        "    raise AssertionError('normal stop must not run cleanup')\n"
        "watch_owner(int(a.owner_pid), int(a.wechat_pid), a.stop_event, a.ready_event, cleanup)\n",
        encoding="utf-8",
    )
    # Only the test's own process is used; this never opens WeChat or loads DLLs.
    guard = lifecycle.BridgeGuard(os.getpid())
    try:
        guard.start_watchdog(script)
        assert guard.child.poll() is None
    finally:
        guard.close()
    assert guard.child.returncode == 0


@pytest.mark.parametrize(("status", "message"), [
    (258, "等待就绪超时（10 秒）"),
    (42, "等待就绪失败（状态 42）"),
])
def test_watchdog_wait_failure_reports_reason_and_absolute_logs(tmp_path, monkeypatch, lifecycle, status, message):
    native = tmp_path / "helper" / "native"
    native.mkdir(parents=True)
    guard = object.__new__(lifecycle.BridgeGuard)
    guard.pid = 123
    guard.k = Mock()
    guard.k.CreateEventW.return_value = 1
    guard.k.WaitForMultipleObjects.return_value = status
    child = Mock(_handle=2)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", Mock(return_value=child))
    with pytest.raises(RuntimeError) as error:
        guard.start_watchdog(native / "watchdog.py")
    assert message in str(error.value)
    assert str(native.parent / "logs" / "bridge_watchdog_startup.log") in str(error.value)
    assert str(native.parent / "logs" / "bridge_watchdog.log") in str(error.value)
    child.wait.assert_not_called()


def test_watchdog_wait_api_failure_keeps_windows_error(tmp_path, monkeypatch, lifecycle):
    native = tmp_path / "helper" / "native"
    native.mkdir(parents=True)
    guard = object.__new__(lifecycle.BridgeGuard)
    guard.pid = 123
    guard.k = Mock()
    guard.k.CreateEventW.return_value = 1
    guard.k.WaitForMultipleObjects.return_value = 0xFFFFFFFF
    monkeypatch.setattr(lifecycle.subprocess, "Popen", Mock(return_value=Mock(_handle=2)))
    monkeypatch.setattr(lifecycle.c, "get_last_error", lambda: 5)
    with pytest.raises(OSError) as error:
        guard.start_watchdog(native / "watchdog.py")
    assert error.value.winerror == 5
    assert str(native.parent / "logs" / "bridge_watchdog_startup.log") in str(error.value)
