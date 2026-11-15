"""Drift checks for the generated OneBot action catalog snapshot."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_action_catalog.py"
SNAPSHOT = ROOT / "docs" / "action-catalog.json"

# Adding or removing an action is an intentional public-surface change: update
# this list, the registry, the snapshot and the architecture document together.
EXPECTED_ACTIONS = {
    "can_send_image",
    "can_send_record",
    "get_capabilities",
    "get_forward_msg",
    "get_friend_list",
    "get_friend_msg_history",
    "get_friend_requests",
    "get_group_info",
    "get_group_list",
    "get_group_member_info",
    "get_group_member_list",
    "get_group_msg_history",
    "get_image",
    "get_login_info",
    "get_msg",
    "get_record",
    "get_status",
    "get_stranger_info",
    "get_version_info",
    "send_group_forward_msg",
    "send_group_msg",
    "send_private_forward_msg",
    "send_private_msg",
    "send_msg",
}


def load_script():
    spec = importlib.util.spec_from_file_location("check_action_catalog_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_snapshot_matches_registry_and_regenerates_identically(tmp_path):
    module = load_script()
    expected = module.build_snapshot()
    text = SNAPSHOT.read_text(encoding="utf-8")
    assert json.loads(text) == expected
    assert text.endswith("\n")
    target = tmp_path / "action-catalog.json"
    module.write(target)
    first = target.read_bytes()
    module.write(target)
    assert target.read_bytes() == first
    assert first == module.render(expected).encode("utf-8")
    assert module.check(target) == []


def test_check_reports_missing_and_tampered_snapshots(tmp_path):
    module = load_script()
    assert module.check(tmp_path / "missing.json")
    tampered = tmp_path / "tampered.json"
    tampered.write_text("{}\n", encoding="utf-8")
    problems = module.check(tampered)
    assert problems
    assert any(problem.startswith("不一致字段:") for problem in problems)


def test_snapshot_has_expected_action_names_and_public_metadata():
    snapshot = load_snapshot()
    rows = snapshot["actions"]
    assert [row["name"] for row in rows] == sorted(EXPECTED_ACTIONS)
    for row in rows:
        assert isinstance(row["summary"], str) and row["summary"]
        assert isinstance(row["read_only"], bool)
        assert isinstance(row["returns"], dict)
        assert isinstance(row["examples"], list)
        for name, spec in row["params"].items():
            assert isinstance(name, str) and name
            assert set(spec) == {"kind", "required", "description"}
            assert isinstance(spec["required"], bool)
            assert isinstance(spec["description"], str)
    by_name = {row["name"]: row for row in rows}
    assert by_name["get_status"]["read_only"] is True
    assert by_name["send_private_msg"]["read_only"] is False
    for name in ("send_private_msg", "send_group_msg"):
        assert by_name[name]["examples"], f"{name} must carry an example"


def test_snapshot_includes_element_manifest_and_capability_names():
    snapshot = load_snapshot()
    from wechat_onebot.protocol.element_manifest import (
        ELEMENT_DIRECTIONS,
        ELEMENT_MANIFEST,
        ELEMENT_SEND_CAPABILITIES,
        NATIVE_SEND_TYPES,
    )

    assert snapshot["elements"] == {
        name: dict(directions) for name, directions in sorted(ELEMENT_MANIFEST.items())
    }
    assert snapshot["send_capabilities"] == dict(sorted(ELEMENT_SEND_CAPABILITIES.items()))
    assert set(ELEMENT_SEND_CAPABILITIES) == NATIVE_SEND_TYPES
    for directions in snapshot["elements"].values():
        assert tuple(directions) == ELEMENT_DIRECTIONS


@pytest.mark.asyncio
async def test_runtime_capabilities_expose_the_same_catalog(tmp_path):
    from test_service import make_service

    service, _, mapper, _ = await make_service(tmp_path)
    try:
        data = (await service.dispatch("get_capabilities"))["data"]
        assert data["action_catalog"] == load_snapshot()["actions"]
        assert data["actions"] == sorted(EXPECTED_ACTIONS)
    finally:
        await service.stop()
        mapper.close()


def _compatibility_action_section() -> str:
    text = (ROOT / "docs/onebot-compatibility.md").read_text(encoding="utf-8")
    return text.split("## Supported actions", 1)[1].split("## Messages and media", 1)[0]


def _compatibility_action_mentions() -> set[str]:
    mentions: set[str] = set()
    for line in _compatibility_action_section().splitlines():
        if line.startswith("|"):
            mentions |= set(re.findall(r"`([a-z][a-z0-9_]+)`", line.split("|")[1]))
        elif line.startswith("Optional extensions:"):
            mentions |= set(re.findall(r"`([a-z][a-z0-9_]+)`", line))
    return mentions


def test_compatibility_doc_only_mentions_registered_actions():
    mentioned = _compatibility_action_mentions()
    assert mentioned <= EXPECTED_ACTIONS
    assert {"send_group_msg", "get_group_member_info", "get_capabilities"} <= mentioned


def test_architecture_document_lists_every_registered_action():
    text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    section = text.split("Core action surface", 1)[1].split(
        "The authoritative action list", 1)[0]
    count = int(re.search(r"\((\d+) registered actions\)", section).group(1))
    names = re.findall(r"^- `([a-z][a-z0-9_]+)`", section, re.MULTILINE)
    assert count == len(names)
    assert set(names) == EXPECTED_ACTIONS


def test_ci_runs_the_catalog_drift_check():
    workflow = (ROOT / ".github/workflows/bot.yml").read_text(encoding="utf-8")
    assert "check_action_catalog.py --check" in workflow
