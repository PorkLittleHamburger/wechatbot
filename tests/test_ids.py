from __future__ import annotations

from pathlib import Path

from wechat_onebot.ids import IdMapper


def test_mapping_is_stable_and_persistent(tmp_path: Path):
    path = tmp_path / "ids.sqlite3"
    with IdMapper(path) as mapper:
        first = mapper.get_or_create("user", "wxid_alice")
        second = mapper.get_or_create("user", "wxid_alice")
        group_id = mapper.get_or_create("group", "123@chatroom")
        assert first == second
        assert first != group_id
        assert mapper.reverse("user", first) == "wxid_alice"
    with IdMapper(path) as mapper:
        assert mapper.get_or_create("user", "wxid_alice") == first
        assert mapper.get("group", "123@chatroom") == group_id


def test_kinds_have_independent_mapping_spaces(tmp_path: Path):
    with IdMapper(tmp_path / "ids.sqlite3") as mapper:
        user = mapper.get_or_create("user", "same")
        group = mapper.get_or_create("group", "same")
        assert user > 0 and group > 0
        assert mapper.reverse("user", user) == "same"
        assert mapper.reverse("group", group) == "same"
