from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx
import uvicorn

from wechat_onebot.config import AppConfig
from wechat_onebot.core.runtime import RuntimeManager
from wechat_onebot.webui import create_webui_app


async def run(config_path: Path) -> int:
    config = AppConfig.load(config_path)
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    server = uvicorn.Server(uvicorn.Config(app, host=config.webui.host, port=config.webui.port,
                                            log_level="warning", access_log=False, lifespan="on"))
    if hasattr(server, "install_signal_handlers"):
        server.install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve(), name="live-webui-preflight")
    try:
        for _ in range(150):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.02)
        if not server.started:
            raise RuntimeError("WebUI 服务未能启动")
        async with httpx.AsyncClient(base_url=f"http://{config.webui.host}:{config.webui.port}", timeout=5) as client:
            login = await client.post("/api/login", json={"token": config.webui.access_token})
            login.raise_for_status()
            status = await client.get("/api/status")
            status.raise_for_status()
            payload = status.json()
        runtime = payload["runtimes"][0] if payload.get("runtimes") else None
        result = {
            "webui_status_ok": payload.get("ok") is True,
            "runtime_present": runtime is not None,
            "runtime_online": bool(runtime and runtime.get("online")),
            "network_started": bool(runtime and runtime.get("network", {}).get("started")),
            "last_error": payload.get("lastError", ""),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(value for key, value in result.items() if key != "last_error") and not result["last_error"] else 2
    finally:
        server.should_exit = True
        await asyncio.gather(task, return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run(args.config.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
