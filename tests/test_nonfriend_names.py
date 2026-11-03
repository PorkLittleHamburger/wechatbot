"""Synthetic exact-ID LRU fixtures; no process access or WeChat interaction."""
import struct
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "vendor/helper"


@pytest.fixture
def nonfriend_memory(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    monkeypatch.syspath_prepend(str(HELPER / "tests"))
    from test_contacts import ContactMemory, BASE, SELF, PEER, CACHE
    from test_native_profiles import updated_memory
    from wxbot_framework.contacts import read_contact_name
    from wxbot_framework.group_members import _hash
    head, entry, sentinel, buckets, index, contact, strings = [0x740000000 + n * 0x1000 for n in range(7)]

    def make(name="Non-friend Nick", *, modern=True, friend_name=""):
        ram = ContactMemory(friend_name)
        for address in (head, entry, sentinel, buckets, index, contact, strings):
            ram.add(address, 0x1000)
        ram.write(CACHE + 0x108, struct.pack("<10Q", head, 1, 0, sentinel, 1,
                                          buckets, buckets + 32, buckets + 32, 1, 2))
        ram.write(head, struct.pack("<QQ", entry, entry))
        ram.write(entry, struct.pack("<QQ", head, head))
        ram.s(entry + 0x10, PEER, strings)
        ram.q(entry + 0x30, contact)
        for offset in (0, 16):
            ram.write(buckets + offset, struct.pack("<QQ", sentinel, sentinel))
        ram.write(buckets + (_hash(PEER) & 1) * 16, struct.pack("<QQ", index, index))
        ram.s(index + 0x10, PEER, strings + 128)
        ram.q(index + 0x30, entry)
        ram.s(contact + 8, PEER, strings + 256)
        ram.s(contact + 0xD8, name, strings + 384)
        return updated_memory(ram) if modern else ram

    return make, read_contact_name, BASE, SELF, PEER, CACHE, contact, strings


@pytest.mark.parametrize("name", ["Member", "群成员微信昵称🌸很长", "x" * 64])
def test_nonfriend_nickname_is_read_from_distinct_lru(nonfriend_memory, name):
    make, lookup, base, account, peer, *_ = nonfriend_memory
    assert lookup(make(name).read, base, peer, expected_account=account) == name


def test_friends_still_take_priority_and_unknown_ids_do_not_match(nonfriend_memory):
    make, lookup, base, account, peer, *_ = nonfriend_memory
    ram = make(friend_name="Friend Nick")
    assert lookup(ram.read, base, peer, expected_account=account) == "Friend Nick"
    assert lookup(ram.read, base, "wxid_unknown", expected_account=account) is None
    assert lookup(ram.read, base, peer, expected_account="wxid_other") is None


@pytest.mark.parametrize("name", ["", "x" * 65, "bad\nname", "bad\x7fname", "Member\n", "\nMember"])
def test_invalid_lru_names_are_not_published(nonfriend_memory, name):
    make, lookup, base, account, peer, *_ = nonfriend_memory
    assert lookup(make(name).read, base, peer, expected_account=account) is None


def test_record_identity_and_verified_version_are_required(nonfriend_memory):
    make, lookup, base, account, peer, _, contact, strings = nonfriend_memory
    ram = make()
    ram.s(contact + 8, "wxid_other", strings + 256)
    assert lookup(ram.read, base, peer, expected_account=account) is None
    # Only the 4.1.15.56 LRU has supporting layout evidence.
    assert lookup(make(modern=False).read, base, peer, expected_account=account) is None


@pytest.mark.parametrize("change", ["name", "layout"])
def test_lru_changes_during_read_are_rejected(nonfriend_memory, change):
    make, lookup, base, account, peer, cache, contact, strings = nonfriend_memory
    ram = make()
    changed = False
    def read(address, length):
        nonlocal changed
        result = ram.read(address, length)
        if address == contact + 0xD8 and not changed:
            changed = True
            if change == "name":
                ram.s(contact + 0xD8, "Changed", strings + 384)
            else:
                ram.q(cache + 0x110, 2)
        return result
    assert lookup(read, base, peer, expected_account=account) is None
    assert changed


@pytest.mark.parametrize("offset,value", [(0x110, 10001), (0x128, 0), (0x148, 3)])
def test_malformed_lru_is_unavailable(nonfriend_memory, offset, value):
    make, lookup, base, account, peer, cache, *_ = nonfriend_memory
    ram = make()
    ram.q(cache + offset, value)
    assert lookup(ram.read, base, peer, expected_account=account) is None


def test_account_switch_during_nonfriend_read_is_rejected(nonfriend_memory, monkeypatch):
    from wxbot_framework import contacts
    make, lookup, base, account, peer, _, contact, _ = nonfriend_memory
    ram = make()
    changed = False
    current_account = contacts.read_current_account

    def read(address, length):
        nonlocal changed
        result = ram.read(address, length)
        if address == contact + 0xD8:
            changed = True
        return result

    monkeypatch.setattr(contacts, "read_current_account", lambda *args:
                        "wxid_other" if changed else current_account(*args))
    assert lookup(read, base, peer, expected_account=account) is None
    assert changed
