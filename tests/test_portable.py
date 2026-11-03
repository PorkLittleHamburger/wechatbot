from pathlib import Path
import runpy


def test_portable_copy_excludes_helper_runtime_state(tmp_path):
    build = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "build_portable.py"))
    source = tmp_path / "vendor"
    helper = source / "helper"
    helper.mkdir(parents=True)
    (helper / "module.py").write_text("# packaged source\n", encoding="utf-8")
    requests = helper / ".runtime" / "native_requests"
    requests.mkdir(parents=True)
    (requests / "private-report.json").write_text('{"local_message":"test"}', encoding="utf-8")
    target = tmp_path / "package" / "vendor"
    build["copy_tree"](source, target)
    assert (target / "helper" / "module.py").is_file()
    assert not (target / "helper" / ".runtime").exists()
