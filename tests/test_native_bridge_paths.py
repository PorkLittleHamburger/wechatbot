from pathlib import Path
import sys

import pytest

HELPER = Path(__file__).resolve().parents[1] / "vendor" / "helper"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native paths")


@pytest.fixture
def native(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    monkeypatch.syspath_prepend(str(HELPER / "native"))
    import run_selftest
    return run_selftest


def test_extended_drive_path_is_same_bridge_file(native):
    dll = native.TEXT_DLL.resolve()
    extended = Path("\\\\?\\" + str(dll))
    assert dll != extended.resolve()  # Reproduces the original path comparison failure.
    assert native.bridge_path_matches(dll, extended)
    assert native.bridge_path_matches(extended, dll)
    native.require_bridge_module((123, str(dll)), extended)


def test_extended_unc_and_case_match_without_accessing_network(native, monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self: self)
    assert native.bridge_path_matches(r"\\?\UNC\server\share\bridge.dll", r"\\SERVER\share\bridge.dll")
    assert not native.bridge_path_matches(r"\\?\UNC\server\other\bridge.dll", r"\\server\share\bridge.dll")


def test_missing_module_and_foreign_path_are_rejected(native, tmp_path):
    dll = tmp_path / "our" / "bridge.dll"
    foreign = tmp_path / "other" / "bridge.dll"
    with pytest.raises(RuntimeError, match="模块列表中未找到") as missing:
        native.require_bridge_module(None, dll)
    assert str(dll) in str(missing.value)
    with pytest.raises(RuntimeError, match="模块路径不符") as mismatch:
        native.require_bridge_module((123, str(foreign)), dll)
    assert str(foreign) in str(mismatch.value)
    assert str(dll) in str(mismatch.value)


def test_stale_bridge_cleanup_accepts_extended_path_but_rejects_foreign(native, monkeypatch, tmp_path):
    import filehelper_service as service
    extended = Path("\\\\?\\" + str(native.TEXT_DLL.resolve()))
    monkeypatch.setattr(service, "TEXT_DLL", extended)
    monkeypatch.setattr(service, "modules", lambda pid: {extended.name.lower(): (123, str(native.TEXT_DLL.resolve()))})
    assert service.loaded_project_bridges(123) == [extended]
    monkeypatch.setattr(service, "modules", lambda pid: {extended.name.lower(): (123, str(tmp_path / extended.name))})
    with pytest.raises(RuntimeError, match="其他项目"):
        service.loaded_project_bridges(123)


def test_missing_module_retains_loader_result_without_treating_it_as_success(native):
    with pytest.raises(RuntimeError, match="0x00000000"):
        native.require_bridge_module(None, native.TEXT_DLL, load_result=0)
    with pytest.raises(RuntimeError, match="模块列表中未找到"):
        native.require_bridge_module(None, native.TEXT_DLL, load_result=123)


def test_receive_channel_accepts_extended_path_before_any_process_operations(native, monkeypatch):
    import filehelper_service as service
    extended = Path("\\\\?\\" + str(native.TEXT_DLL.resolve()))
    monkeypatch.setattr(service, "modules", lambda pid: {extended.name.lower(): (123, str(native.TEXT_DLL.resolve()))})

    class BeforeProcessOpen(Exception):
        pass

    def stop_at_pe(path):
        raise BeforeProcessOpen

    monkeypatch.setattr(service.pefile, "PE", stop_at_pe)
    with pytest.raises(BeforeProcessOpen):
        service.ReceiveChannel(123, dll=extended)
