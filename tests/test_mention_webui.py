from __future__ import annotations

from fastapi.testclient import TestClient

from test_webui import login_and_set_password, make_config
from wechat_onebot.core.runtime import RuntimeManager
from wechat_onebot.webui import create_webui_app


def test_mention_label_webui_bind_list_and_delete(tmp_path):
    config = make_config(tmp_path)
    manager = RuntimeManager(config)
    app = create_webui_app(config, manager)
    with TestClient(app) as client:
        login_and_set_password(client)
        assert client.post("/api/accounts/wxid_robot/start").json()["ok"] is True
        groups = client.post("/api/accounts/wxid_robot/action", json={
            "action": "get_group_list", "params": {}}).json()["data"]
        group_id = groups[0]["group_id"]
        members = client.post("/api/accounts/wxid_robot/action", json={
            "action": "get_group_member_list", "params": {"group_id": group_id}}).json()["data"]
        user_id = members[0]["user_id"]

        response = client.put("/api/accounts/wxid_robot/mention-labels", json={
            "group_id": group_id, "user_id": user_id, "label": "群里的真实名"})
        assert response.status_code == 200
        assert response.json()["ok"] is True

        listed = client.get("/api/accounts/wxid_robot/mention-labels").json()["labels"]
        assert listed == [{"group_id": group_id, "user_id": user_id,
                           "label": "群里的真实名"}]
        by_group = client.get(
            "/api/accounts/wxid_robot/mention-labels",
            params={"group_id": group_id}).json()["labels"]
        assert by_group == listed

        deleted = client.request("DELETE", "/api/accounts/wxid_robot/mention-labels",
                                 json={"group_id": group_id, "user_id": user_id})
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True
        assert client.get("/api/accounts/wxid_robot/mention-labels").json()["labels"] == []

        invalid = client.put("/api/accounts/wxid_robot/mention-labels", json={
            "group_id": group_id, "user_id": user_id, "label": "   "})
        assert invalid.status_code == 400
