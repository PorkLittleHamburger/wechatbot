from __future__ import annotations

from pathlib import Path

from wechat_onebot.onebot import MessageStore, hash_message_id_int32


def test_message_ids_are_signed_nonzero_and_stable(tmp_path: Path):
    path = tmp_path / "messages.sqlite3"
    with MessageStore(path) as store:
        first = store.get_or_allocate_id(
            account="wxid_robot",
            event_name="group_message",
            session_key="123@chatroom",
            sequence_key="42",
        )
        second = store.get_or_allocate_id(
            account="wxid_robot",
            event_name="group_message",
            session_key="123@chatroom",
            sequence_key="42",
        )
        assert first == second
        assert first != 0
        assert -(2**31) <= first <= 2**31 - 1
        assert first == hash_message_id_int32("42", "123@chatroom", "group_message")
        store.put(first, account="wxid_robot", event_name="group_message",
                  session_key="123@chatroom", payload={"message_id": first, "x": 1})
    with MessageStore(path) as store:
        assert store.get(first, account="wxid_robot") == {"message_id": first, "x": 1}
        assert store.get_or_allocate_id(
            account="wxid_robot",
            event_name="group_message",
            session_key="123@chatroom",
            sequence_key="42",
        ) == first


def test_collision_resolves_to_another_signed_id(tmp_path: Path):
    class ForcedHash:
        def __init__(self):
            self.calls = 0

        def __call__(self, sequence, session_id, event_name, salt=0):
            self.calls += 1
            return 99 if self.calls == 1 else 100

    original = __import__("wechat_onebot.onebot.message_store", fromlist=["hash_message_id_int32"]).hash_message_id_int32
    module = __import__("wechat_onebot.onebot.message_store", fromlist=["hash_message_id_int32"])
    module.hash_message_id_int32 = ForcedHash()
    try:
        with MessageStore(tmp_path / "collision.sqlite3") as store:
            first = store.get_or_allocate_id(account="a", event_name="m", session_key="s1", sequence_key="1")
            second = store.get_or_allocate_id(account="a", event_name="m", session_key="s2", sequence_key="2")
            assert first == 99
            assert second != first
    finally:
        module.hash_message_id_int32 = original
