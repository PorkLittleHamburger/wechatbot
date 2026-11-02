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
            response = await client.get("/healthz")
            response.raise_for_status()
            payload = response.json()
        result = {
            "webui_status_ok": payload.get("ok") is True,
            "runtimes_at_start": payload.get("runtimes"),
            "last_error": payload.get("lastError", ""),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["webui_status_ok"] and result["runtimes_at_start"] == 0 else 2
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
