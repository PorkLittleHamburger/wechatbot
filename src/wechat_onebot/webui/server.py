from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from dataclasses import replace

from ..config import AppConfig, account_directories, update_account
from ..onebot.network import AccountNetworkConfig, load_or_create_account_network_config
from ..core.runtime import RuntimeManager
from ..bridge import discover_logged_accounts
from .auth import PasswordStore
from .startup import autostart_task, load_last_bridge, save_last_bridge


COOKIE_NAME = "wechat_onebot_webui"
SESSION_SECONDS = 12 * 60 * 60
LOG_LIMIT = 200
RESUME_CHECK_SECONDS = 1.0


class _SafeRuntimeLogs(logging.Handler):
    """Keep diagnostic summaries, never formatted log arguments or exceptions."""

    _messages = {
        "反向 WebSocket 已连接: %s": "反向 WebSocket 已连接",
        "反向 WebSocket 连接失败 %s: %s；%.1fs 后重试": "反向 WebSocket 连接失败，等待重试",
        "HTTP 上报队列已满，丢弃事件: %s": "HTTP 上报队列已满",
        "HTTP 上报失败 %s: %s": "HTTP 上报失败",
        "OneBot 动作失败: %s": "OneBot 动作失败",
    }

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.entries: deque[dict[str, str]] = deque(maxlen=LOG_LIMIT)

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("wechat-onebot."):
            return
        scope = record.name.removeprefix("wechat-onebot.")
        if not scope or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in scope):
            scope = "runtime"
        self.entries.append({
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "scope": scope,
            "message": self._messages.get(record.msg, "运行异常" if record.levelno >= logging.ERROR else "运行事件"),
        })


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, tuple[float, str]] = {}

    def create(self, kind: str = "full") -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (time.time() + SESSION_SECONDS, kind)
        return token

    def valid(self, token: str, kind: str = "full") -> bool:
        entry = self._sessions.get(token)
        if entry is None:
            return False
        expires, stored_kind = entry
        if expires < time.time():
            self._sessions.pop(token, None)
            return False
        return stored_kind == kind

    def revoke(self, token: str) -> None:
        self._sessions.pop(token, None)

    def revoke_all(self) -> None:
        self._sessions.clear()


def create_webui_app(config: AppConfig, manager: RuntimeManager, *, start_runtime: bool = False) -> FastAPI:
    sessions = SessionStore()
    passwords = PasswordStore(config.storage.data_dir / "webui-auth.json")
    runtime_logs = _SafeRuntimeLogs()
    panel_path = Path(__file__).resolve().parents[3] / "webui" / "index.html"
    icon_path = panel_path.parent.parent / "assets" / "icons" / "wechat-onebot.svg"

    def authorized(request: Request) -> bool:
        cookie = request.cookies.get(COOKIE_NAME, "")
        return bool(cookie) and sessions.valid(cookie)

    def status_payload() -> dict[str, Any]:
        return {
            "ok": True,
            "app": config.app_name,
            "version": config.version,
            "driver": config.driver.type,
            "configuredAccount": config.driver.wechat_account,
            "configuredNickname": config.driver.nickname,
            "configuredPid": config.driver.wechat_pid,
            "lastError": manager.last_error,
            "runtimes": manager.snapshot(),
        }

    async def resume_last_bridge() -> None:
        account = load_last_bridge(config.storage.data_dir)
        if not account:
            return
        while load_last_bridge(config.storage.data_dir) == account:
            current = manager.get(account)
            if current is not None and current.started:
                return
            if config.driver.type == "helper":
                try:
                    accounts = await asyncio.to_thread(discover_logged_accounts, config.driver.helper_root)
                except Exception:
                    manager.last_error = "读取微信登录状态失败，正在重试"
                    await asyncio.sleep(RESUME_CHECK_SECONDS)
                    continue
                if not any(row.wxid == account for row in accounts):
                    manager.last_error = "等待上次桥接账号登录"
                    await asyncio.sleep(RESUME_CHECK_SECONDS)
                    continue
            try:
                await manager.start_account(account)
                manager.last_error = ""
                return
            except Exception:
                manager.last_error = "上次桥接账号已登录，桥接启动失败，正在重试"
                await asyncio.sleep(RESUME_CHECK_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        root_logger = logging.getLogger()
        root_logger.addHandler(runtime_logs)
        resume_task = None
        try:
            try:
                await manager.start_saved_networks()
            except Exception as error:
                manager.last_error = str(error)
            if start_runtime:
                resume_task = asyncio.create_task(resume_last_bridge())
            yield
        finally:
            if resume_task is not None:
                resume_task.cancel()
                with suppress(asyncio.CancelledError):
                    await resume_task
            root_logger.removeHandler(runtime_logs)
            await manager.stop_all()

    app = FastAPI(title="WeChat OneBot WebUI", version=config.version, lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "runtimes": len(manager.snapshot()), "lastError": manager.last_error}

    @app.get("/")
    async def panel():
        if not panel_path.is_file():
            return Response("webui/index.html not found", status_code=500, media_type="text/plain")
        return FileResponse(panel_path, media_type="text/html")

    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(icon_path, media_type="image/svg+xml")

    @app.get("/api/auth-state")
    async def auth_state():
        return {"setupRequired": not passwords.configured}

    @app.post("/api/login")
    async def login(request: Request, response: Response):
        try:
            body = await request.json()
        except Exception:
            return Response("请求体必须是 JSON", status_code=400)
        if not isinstance(body, dict):
            return Response("请求体必须是 JSON 对象", status_code=400)
        if passwords.configured:
            password = body.get("password")
            if not isinstance(password, str) or not passwords.verify(password):
                return Response("密码错误", status_code=401)
            kind = "full"
        else:
            token = body.get("token")
            if (not isinstance(token, str) or not config.webui.access_token
                    or not hmac.compare_digest(token, config.webui.access_token)):
                return Response("首次登录 Token 无效", status_code=401)
            kind = "setup"
        session = sessions.create(kind)
        response.set_cookie(COOKIE_NAME, session, max_age=SESSION_SECONDS, httponly=True,
                            samesite="strict", secure=False)
        return {"ok": True, "expiresIn": SESSION_SECONDS, "setupRequired": kind == "setup"}

    @app.post("/api/setup-password")
    async def setup_password(request: Request, response: Response):
        cookie = request.cookies.get(COOKIE_NAME, "")
        if passwords.configured or not cookie or not sessions.valid(cookie, "setup"):
            return Response("首次登录验证已失效", status_code=401)
        try:
            body = await request.json()
        except Exception:
            return Response("请求体必须是 JSON", status_code=400)
        password = body.get("password") if isinstance(body, dict) else None
        if not isinstance(password, str):
            return Response("请输入密码", status_code=400)
        try:
            passwords.set(password)
        except ValueError as error:
            return Response(str(error), status_code=400)
        sessions.revoke_all()
        response.set_cookie(COOKIE_NAME, sessions.create(), max_age=SESSION_SECONDS,
                            httponly=True, samesite="strict", secure=False)
        return {"ok": True}

    @app.post("/api/change-password")
    async def change_password(request: Request, response: Response):
        if not authorized(request):
            return Response("未登录", status_code=401)
        try:
            body = await request.json()
        except Exception:
            return Response("请求体必须是 JSON", status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("currentPassword"), str):
            return Response("请输入当前密码", status_code=400)
        if not passwords.verify(body["currentPassword"]):
            return Response("当前密码错误", status_code=401)
        new_password = body.get("newPassword")
        if not isinstance(new_password, str):
            return Response("请输入新密码", status_code=400)
        try:
            passwords.set(new_password)
        except ValueError as error:
            return Response(str(error), status_code=400)
        sessions.revoke_all()
        response.set_cookie(COOKIE_NAME, sessions.create(), max_age=SESSION_SECONDS,
                            httponly=True, samesite="strict", secure=False)
        return {"ok": True}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response):
        cookie = request.cookies.get(COOKIE_NAME, "")
        if cookie:
            sessions.revoke(cookie)
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @app.get("/api/status")
    async def status(request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        return status_payload()

    @app.get("/api/processes")
    async def processes(request: Request, scan: bool = False):
        if not authorized(request):
            return Response("未登录", status_code=401)
        rows: dict[str, dict[str, Any]] = {}
        configured = config.driver.wechat_account
        if configured:
            rows[configured] = {"account": configured, "pid": config.driver.wechat_pid,
                                "nickname": config.driver.nickname, "configured": True,
                                "running": False, "online": False, "started": False}
        for runtime in manager.snapshot():
            account = str(runtime["account"])
            row = rows.setdefault(account, {"account": account, "pid": None, "nickname": "",
                                             "configured": account == configured, "running": False,
                                             "online": False, "started": False})
            row.update(pid=runtime["pid"] if runtime["pid"] is not None else row["pid"],
                       running=runtime["started"],
                       online=runtime["online"], started=runtime["started"])
        error = None
        if scan and config.driver.type == "helper":
            try:
                discovered = await asyncio.to_thread(discover_logged_accounts, config.driver.helper_root)
                for login in discovered:
                    row = rows.setdefault(login.wxid, {"account": login.wxid, "pid": login.pid,
                                                       "nickname": login.nickname, "configured": login.wxid == configured,
                                                       "running": False, "online": False, "started": False})
                    if not row["running"]:
                        row["pid"] = login.pid
                    row["nickname"] = login.nickname
            except Exception:
                error = "只读账号扫描失败"
        result: dict[str, Any] = {"processes": list(rows.values())}
        if error:
            result["error"] = error
        return result

    @app.get("/api/autostart")
    async def autostart_status(request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        task = await asyncio.to_thread(autostart_task, "status")
        return {**task, "lastAccount": load_last_bridge(config.storage.data_dir)}

    @app.post("/api/autostart")
    async def autostart_update(request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        body = await request.json()
        if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
            return Response("enabled 必须是布尔值", status_code=400)
        if body["enabled"] and config.driver.type != "helper":
            return Response("Mock 模式不能启用真实开机自启", status_code=409)
        task = await asyncio.to_thread(autostart_task, "install" if body["enabled"] else "remove")
        return {**task, "lastAccount": load_last_bridge(config.storage.data_dir)}

    @app.post("/api/account-selection")
    async def account_selection(request: Request):
        nonlocal config
        if not authorized(request):
            return Response("未登录", status_code=401)
        try:
            body = await request.json()
        except ValueError:
            return Response("请求体必须是 JSON", status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("account"), str):
            return Response("请选择有效账号", status_code=400)
        account = body["account"].strip()
        if not account:
            return Response("请选择有效账号", status_code=400)
        if config.driver.type == "helper":
            try:
                discovered = await asyncio.to_thread(discover_logged_accounts, config.driver.helper_root)
            except Exception:
                return Response("只读账号扫描失败", status_code=503)
            matches = [row for row in discovered if row.wxid == account]
            if len(matches) != 1:
                return Response("账号未登录或无法唯一识别，请重新扫描", status_code=409)
            selected = matches[0]
            pid, nickname = selected.pid, selected.nickname
        else:
            if account != config.driver.wechat_account:
                return Response("Mock 模式只能选择已配置账号", status_code=409)
            pid, nickname = config.driver.wechat_pid, config.driver.nickname
        update_account(config.path, account=account, pid=pid, nickname=nickname)
        config = replace(config, driver=replace(config.driver, wechat_account=account,
                                                wechat_pid=pid, nickname=nickname))
        manager.config = config
        manager.last_error = ""
        try:
            await manager.start_saved_networks()
        except Exception as error:
            manager.last_error = str(error)
        return {"ok": True, "account": account, "pid": pid, "nickname": nickname}

    @app.get("/api/logs")
    async def logs(request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        return {"logs": list(runtime_logs.entries)}

    @app.get("/api/events")
    async def events(request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)

        async def stream():
            while True:
                if await request.is_disconnected():
                    return
                payload = json.dumps(status_payload(), ensure_ascii=False, separators=(",", ":"))
                yield f"data: {payload}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/accounts/{account}/network")
    async def account_network(account: str, request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        account_dir, _ = account_directories(config, account)
        network_config = load_or_create_account_network_config(account_dir / "onebot.json", config, account)
        return network_config.to_dict()

    @app.put("/api/accounts/{account}/network")
    async def account_network_save(account: str, request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        try:
            body = await request.json()
        except Exception:
            return Response("请求体必须是 JSON", status_code=400)
        if not isinstance(body, dict):
            return Response("网络配置必须是 JSON 对象", status_code=400)
        try:
            network_config = AccountNetworkConfig.from_dict(body)
            applied = await manager.apply_network_config(account, network_config)
        except Exception as error:
            return {"ok": False, "saved": False, "applied": False, "message": str(error)}
        return {"ok": True, "saved": True, "applied": applied,
                "message": "网络配置已保存并实时生效" if applied else "网络配置已保存"}

    @app.get("/api/accounts/{account}/events")
    async def account_events(account: str, request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        runtime = manager.get(account)
        if runtime is None:
            return Response("账号 Runtime 不存在", status_code=404)
        return {"events": runtime.service.recent_events(100)}

    @app.get("/api/accounts/{account}/storage")
    async def account_storage(account: str, request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        runtime = manager.get(account)
        if runtime is None:
            return Response("账号 Runtime 不存在", status_code=404)
        message_stats = runtime.message_store.stats()
        media_files = [path for path in runtime.media_dir.rglob("*") if path.is_file()]
        return {
            "accountDir": str(runtime.account_dir),
            "mediaDir": str(runtime.media_dir),
            "messages": message_stats,
            "mediaFileCount": len(media_files),
            "mediaBytes": sum(path.stat().st_size for path in media_files),
        }

    @app.post("/api/accounts/{account}/action")
    async def account_action(account: str, request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        try:
            body = await request.json()
        except Exception:
            return Response("请求体必须是 JSON", status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("action"), str):
            return Response("请求必须包含 action", status_code=400)
        return await manager.dispatch(account, body["action"], body.get("params"), body.get("echo"))

    @app.post("/api/accounts/{account}/start")
    async def account_start(account: str, request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        pid = config.driver.wechat_pid if account == config.driver.wechat_account else None
        try:
            await manager.start_account(account, pid)
            try:
                save_last_bridge(config.storage.data_dir, account)
            except OSError:
                manager.last_error = "桥接已启动，但无法保存下次自启账号"
                return {"ok": True, "warning": manager.last_error}
            manager.last_error = ""
            return {"ok": True}
        except Exception as error:
            manager.last_error = str(error)
            return {"ok": False, "error": str(error)}

    @app.post("/api/accounts/{account}/stop")
    async def account_stop(account: str, request: Request):
        if not authorized(request):
            return Response("未登录", status_code=401)
        await manager.stop_account(account)
        return {"ok": True}

    return app
