from __future__ import annotations

import sys
from pathlib import Path

from .models import WeChatLogin


def discover_logged_accounts(helper_root: str | Path) -> list[WeChatLogin]:
    """Explicit, event-driven account discovery.

    This function is intentionally not wrapped in a polling loop. Callers invoke
    it on startup, manual refresh, or a trusted login/process event.
    """
    if sys.platform != "win32":
        return []
    root = Path(helper_root).resolve()
    if not (root / "wxbot_framework" / "startup.py").is_file():
        raise RuntimeError(f"helper 目录不完整: {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from wxbot_framework.startup import scan_logged_accounts

    rows = scan_logged_accounts(root)
    return [
        WeChatLogin(pid=int(row["pid"]), wxid=str(row["wxid"]),
                    nickname=str(row.get("name") or ""))
        for row in rows
    ]
