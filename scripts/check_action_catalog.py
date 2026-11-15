"""Generate or verify the machine-readable OneBot action catalog snapshot.

``src/wechat_onebot/onebot/actions.py`` owns the action registry and its public
metadata. ``docs/action-catalog.json`` is a generated snapshot that CI compares
against the registry, so documentation and consumers cannot drift silently.

Usage (from the repository root, with the project environment active):

    python scripts/check_action_catalog.py --write   # regenerate the snapshot
    python scripts/check_action_catalog.py --check   # fail on drift (default)

Element direction support and the element -> driver capability mapping are also
included because they are part of the same public action/segment contract; their
single source is ``src/wechat_onebot/protocol/element_manifest.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# A repository maintenance script must read the checkout, not an unrelated
# installed copy of the package.
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from wechat_onebot.onebot.actions import action_catalog  # noqa: E402
from wechat_onebot.protocol.element_manifest import (  # noqa: E402
    ELEMENT_MANIFEST,
    ELEMENT_SEND_CAPABILITIES,
)

CATALOG_PATH = ROOT / "docs" / "action-catalog.json"
SCHEMA_VERSION = 1


def build_snapshot() -> dict[str, Any]:
    """Build the deterministic snapshot object compared by ``--check``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "scripts/check_action_catalog.py --write",
        "actions": action_catalog(),
        "elements": {
            name: dict(directions) for name, directions in sorted(ELEMENT_MANIFEST.items())
        },
        "send_capabilities": dict(sorted(ELEMENT_SEND_CAPABILITIES.items())),
    }


def render(snapshot: dict[str, Any] | None = None) -> str:
    """Serialize the snapshot as stable UTF-8 JSON with a trailing newline."""
    value = build_snapshot() if snapshot is None else snapshot
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write(path: Path = CATALOG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8", newline="\n")
    return path


def check(path: Path = CATALOG_PATH) -> list[str]:
    """Return drift problems; an empty list means the snapshot is current."""
    if not path.is_file():
        return [
            f"缺少动作目录快照: {path}",
            "运行 python scripts/check_action_catalog.py --write 生成并提交该文件",
        ]
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [
            f"动作目录快照无法解析: {error}",
            "运行 python scripts/check_action_catalog.py --write 重新生成",
        ]
    expected = build_snapshot()
    if actual == expected:
        return []
    problems = [
        "动作目录快照与 action_catalog()/element_manifest 不一致",
        "运行 python scripts/check_action_catalog.py --write 后提交 docs/action-catalog.json",
    ]
    for key in ("schema_version", "generated_by", "actions", "elements", "send_capabilities"):
        if actual.get(key) != expected[key]:
            problems.append(f"不一致字段: {key}")
    unknown = sorted(set(actual) - set(expected))
    if unknown:
        problems.append(f"快照包含未知字段: {', '.join(unknown)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成或校验 OneBot 动作目录快照")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="重新生成 docs/action-catalog.json")
    mode.add_argument("--check", action="store_true",
                      help="校验快照未漂移；未指定模式时默认执行")
    args = parser.parse_args(argv)
    if args.write:
        path = write()
        print(f"已写入 {path.relative_to(ROOT).as_posix()}")
        return 0
    problems = check()
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
