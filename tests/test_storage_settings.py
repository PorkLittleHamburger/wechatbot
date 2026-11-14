from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from test_webui import login_and_set_password, make_config
from wechat_onebot.core.runtime import RuntimeManager
from wechat_onebot.webui import create_webui_app


def test_media_size_limit_can_be_changed_from_webui(tmp_path):
    config = make_config(tmp_path)
    config = replace(config, storage=replace(config.storage,
                                             max_media_bytes=50 * 1024 * 1024))
    config.path.write_text(json.dumps({
        "onebot": {"access_token": "test-token"},
        "reverse_ws": [],
        "driver": {"type": "mock", "helper_root": str(tmp_path / "helper"),
                   "wechat_account": "wxid_robot"},
        "storage": {"data_dir": str(tmp_path / "data"),
                    "media_dir": str(tmp_path / "media"),
                    "max_media_bytes": 50 * 1024 * 1024},
    }), encoding="utf-8")
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    with TestClient(app) as client:
        login_and_set_password(client)
        assert client.get("/api/storage").json() == {"maxMediaBytes": 50 * 1024 * 1024}
        assert client.post("/api/accounts/wxid_robot/start").json()["ok"] is True
        runtime = manager.get("wxid_robot")
        assert runtime is not None
        assert runtime.media.max_bytes == 50 * 1024 * 1024

        changed = client.put("/api/storage", json={"maxMediaBytes": 80 * 1024 * 1024})
        assert changed.status_code == 200
        assert changed.json() == {"ok": True, "maxMediaBytes": 80 * 1024 * 1024}
        assert client.get("/api/storage").json() == {"maxMediaBytes": 80 * 1024 * 1024}
        assert manager.get("wxid_robot").media.max_bytes == 80 * 1024 * 1024
        raw = json.loads(config.path.read_text(encoding="utf-8"))
        assert raw["storage"]["max_media_bytes"] == 80 * 1024 * 1024

        assert client.put("/api/storage", json={"maxMediaBytes": 0}).status_code == 400
        assert client.put("/api/storage", json={"maxMediaBytes": 1024 ** 3 + 1}).status_code == 400
