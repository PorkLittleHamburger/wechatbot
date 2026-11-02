from __future__ import annotations

import importlib
import json
from pathlib import Path


def test_fresh_runtime_stages_source_before_python_import_check(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    staging = importlib.import_module("stage_desktop_runtime")
    root = tmp_path / "project"
    for name, content in {
        "src/wechat_onebot/__init__.py": '__version__ = "0.8.0"\n',
        "src/wechat_onebot/launcher/worker.py": "# offline fixture\n",
        "webui/index.html": "<html>fixture</html>",
        "THIRD_PARTY_NOTICES.md": "fixture\n",
        "requirements-runtime.lock": "fixture==1.0\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for name in ("vendor", "assets", "dist"):
        (root / name).mkdir()
    calls = []

    def fresh_python(target):
        # This models the real bundled interpreter's import smoke check and
        # fails if source is copied only after bundle_python returns.
        assert (target / "src/wechat_onebot/__init__.py").is_file()
        calls.append(target)
        (target / "python").mkdir()
        (target / "python/python.exe").write_bytes(b"offline package fixture")

    monkeypatch.setattr(staging, "ROOT", root)
    monkeypatch.setattr(staging, "bundle_python", fresh_python)
    monkeypatch.setenv("WECHAT_BUILD_ROOT", str(tmp_path / "build"))
    staging.main()
    assert len(calls) == 1
    installed = root / "desktop/src-tauri/resources/runtime"
    manifest = json.loads((installed / "VERSION_MANIFEST.json").read_text())
    assert "src/wechat_onebot/__init__.py" in manifest["files"]
    assert (root / "dist/wechat-onebot-runtime-0.8.0-windows-x64.zip").is_file()
