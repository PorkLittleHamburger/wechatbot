from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from wechat_onebot.config import AppConfig
from wechat_onebot.core.runtime import AccountRuntime


async def run(config_path: Path) -> int:
    config = AppConfig.load(config_path)
    runtime = AccountRuntime.create(config)
    try:
        await asyncio.wait_for(runtime.start(), timeout=60)
        login = await runtime.service.dispatch("get_login_info", {})
        status = await runtime.service.dispatch("get_status", {})
        friends = await runtime.service.dispatch("get_friend_list", {})
        capabilities = await runtime.service.dispatch("get_capabilities", {})
        filehelper_id = runtime.mapper.get_or_create("user", "filehelper")
        text = f"wechat-onebot full-stack preflight {time.strftime('%Y-%m-%d %H:%M:%S')}"
        sent = await asyncio.wait_for(runtime.service.dispatch("send_private_msg", {
            "user_id": filehelper_id,
            "message": text,
            "echo": "full-stack",
        }), timeout=30)
        result = {
            "login_ok": login.get("status") == "ok",
            "status_ok": status.get("status") == "ok" and status.get("data", {}).get("online") is True,
            "friend_count": len(friends.get("data", [])) if friends.get("status") == "ok" else None,
            "action_count": len(capabilities.get("data", {}).get("actions", [])) if capabilities.get("status") == "ok" else None,
            "send_status": sent.get("status"),
            "send_retcode": sent.get("retcode"),
            "message_id": sent.get("data", {}).get("message_id"),
            "text": text,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status_ok"] and result["send_status"] == "ok" else 2
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
