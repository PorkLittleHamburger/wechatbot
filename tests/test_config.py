from __future__ import annotations

import json
from pathlib import Path

from wechat_onebot.config import AppConfig


def test_wechat_data_directory_override_is_resolved_from_config_directory(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "onebot": {"access_token": "test"},
        "reverse_ws": [],
        "driver": {
            "type": "helper",
            "wechat_account": "wxid_robot",
            "wechat_pid": 123,
            "wechat_data_directory": "wechat-data",
        },
        "storage": {"data_dir": "data", "media_dir": "media"},
        "webui": {"enabled": True, "access_token": "webui"},
    }), encoding="utf-8")
    config = AppConfig.load(path)
    assert config.driver.wechat_data_directory == (tmp_path / "wechat-data").resolve()


def test_sender_card_as_nickname_defaults_on_and_accepts_false(tmp_path: Path):
    base = {
        "onebot": {"access_token": "test"},
        "reverse_ws": [],
        "driver": {"type": "helper", "wechat_account": "wxid_robot", "wechat_pid": 123},
        "storage": {"data_dir": "data", "media_dir": "media"},
        "webui": {"enabled": True, "access_token": "webui"},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    assert AppConfig.load(path).driver.sender_card_as_nickname is True

    base["driver"]["sender_card_as_nickname"] = False
    path.write_text(json.dumps(base), encoding="utf-8")
    assert AppConfig.load(path).driver.sender_card_as_nickname is False
