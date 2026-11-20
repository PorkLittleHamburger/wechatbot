from __future__ import annotations

from fastapi.testclient import TestClient

from test_webui import login_and_set_password, make_config
from wechat_onebot.core.runtime import RuntimeManager
from wechat_onebot.webui import create_webui_app


def test_manual_mention_label_api_is_gone(tmp_path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    with TestClient(app) as client:
        login_and_set_password(client)
        assert client.post("/api/accounts/wxid_robot/start").json()["ok"] is True
        url = "/api/accounts/wxid_robot/mention-labels"
        assert client.get(url).status_code == 404
        assert client.put(url, json={"group_id": 1, "user_id": 2,
                                     "label": "群里的真实名"}).status_code == 404
        assert client.request("DELETE", url, json={
            "group_id": 1, "user_id": 2}).status_code == 404


def test_webui_panel_has_no_manual_mention_label_card(tmp_path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    with TestClient(app) as client:
        login_and_set_password(client)
        response = client.get("/")
        assert response.status_code == 200
        html = response.text
        for removed in ("成员 @ 显示名", "mention-labels", "mentionList",
                        "mentionGroup", "mention-save", "mention-delete"):
            assert removed not in html
