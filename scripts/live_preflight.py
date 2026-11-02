from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import replace
from pathlib import Path

from wechat_onebot.config import AppConfig
from wechat_onebot.driver.helper import HelperDriver


async def run(config_path: Path, send: bool) -> int:
    config = AppConfig.load(config_path)
    if config.driver.type != "helper":
        raise SystemExit("live_preflight 只允许 helper 驱动")
    config = replace(config, driver=replace(config.driver, type="helper"))
    driver = HelperDriver(config)
    received = []

    async def on_event(event):
        received.append(type(event).__name__)

    started = False
    try:
        logging.getLogger("live-preflight").info("正在加载原生桥接，PID=%s，账号=%s",
                                                 config.driver.wechat_pid, config.driver.wechat_account)
        await asyncio.wait_for(driver.start(on_event), timeout=60)
        started = True
        info = await driver.get_login_info()
        status = await driver.get_status()
        result = {
            "login": {"user_id": info.external_user_id, "nickname": info.nickname},
            "status": {"online": status.online, "good": status.good, "message": status.message,
                       "details": status.details},
            "received_event_types": received,
        }
        try:
            friends = await asyncio.wait_for(driver.get_friend_list(), timeout=30)
            result["friend_count"] = len(friends)
        except Exception as error:
            result["friend_error"] = str(error)
        try:
            groups = await asyncio.wait_for(driver.get_group_list(), timeout=30)
            result["group_count"] = len(groups)
        except Exception as error:
            result["group_error"] = str(error)
        if send:
            text = f"wechat-onebot native bridge preflight {time.strftime('%Y-%m-%d %H:%M:%S')}"
            accepted = await asyncio.wait_for(driver.send_text("filehelper", text), timeout=30)
            result["filehelper_send"] = {"accepted": accepted, "text": text}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if status.online and status.good else 2
    finally:
        if started:
            await driver.stop()
        logging.getLogger("live-preflight").info("原生桥接预检结束")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--send-filehelper", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run(args.config.resolve(), args.send_filehelper))


if __name__ == "__main__":
    raise SystemExit(main())
