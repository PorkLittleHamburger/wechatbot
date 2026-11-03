from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx
import websockets

from wechat_onebot.config import AppConfig
from wechat_onebot.core.runtime import AccountRuntime


async def run(config_path: Path) -> int:
    config = AppConfig.load(config_path)
    runtime = AccountRuntime.create(config)
    await runtime.start()
    try:
        http_endpoint = next(row for row in runtime.network.config.http_servers if row.enabled)
        ws_endpoint = next(row for row in runtime.network.config.ws_servers if row.enabled)
        token = http_endpoint.access_token
        async with httpx.AsyncClient(base_url=f"http://{http_endpoint.host}:{http_endpoint.port}", timeout=5) as client:
            response = await client.post("/", headers={"Authorization": f"Bearer {token}"},
                                         json={"action": "get_status"})
            response.raise_for_status()
            status_response = response.json()
        ws_headers = {"Authorization": f"Bearer {ws_endpoint.access_token}"} if ws_endpoint.access_token else None
        uri = f"ws://{ws_endpoint.host}:{ws_endpoint.port}{ws_endpoint.path}"
        async with websockets.connect(uri, additional_headers=ws_headers) as ws:
            lifecycle = json.loads(await asyncio.wait_for(ws.recv(), 3))
            await ws.send(json.dumps({"action": "get_login_info", "echo": "live-runtime"}))
            ws_response = json.loads(await asyncio.wait_for(ws.recv(), 5))
        result = {
            "http_status_ok": status_response.get("status") == "ok",
            "ws_lifecycle_ok": lifecycle.get("meta_event_type") == "lifecycle",
            "ws_action_ok": ws_response.get("status") == "ok" and ws_response.get("echo") == "live-runtime",
            "network_started": runtime.network.started,
            "online": runtime.driver.online,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(result.values()) else 2
    finally:
        await runtime.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run(args.config.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
