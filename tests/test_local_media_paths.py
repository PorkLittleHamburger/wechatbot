import struct
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "vendor" / "helper"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows extended local paths")


@pytest.fixture
def media(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    from wxbot_framework import files, voices
    return files, voices


def extended(path):
    return Path("\\\\?\\" + str(path.resolve()))


def test_file_store_startup_accepts_extended_workspace(tmp_path, media):
    from wxbot_framework.file_metadata import FileStore
    root = extended(tmp_path / "workspace with spaces" / "files")
    store = FileStore(root, "wxid_offline")
    assert store.root == root
    store._prepare_directory()
    assert store.directory.is_dir()


def test_file_and_voice_snapshots_with_extended_source_and_destination(tmp_path, media):
    files, voices = media
    file = tmp_path / "中文 file.txt"
    file.write_bytes(b"offline sample")
    assert files.inspect_file(extended(file)).path == extended(file)
    with files.prepare_file(extended(file), extended(tmp_path / "file snapshots")) as info:
        assert info.path.read_bytes() == file.read_bytes()
        owned = info.path.parent
    assert not owned.exists()
    voice = tmp_path / "voice.silk"
    voice.write_bytes(voices.TENCENT_SILK_HEADER + struct.pack("<h", 3) + b"abc")
    assert voices.inspect_voice_file(extended(voice)).duration_ms == 20
    with voices.prepare_voice(extended(voice), extended(tmp_path / "voice snapshots")) as info:
        assert info.path.read_bytes() == voice.read_bytes()
        owned = info.path.parent
    assert not owned.exists()


@pytest.mark.parametrize("value", [
    r"\\server\share\file", r"\\?\UNC\server\share\file", "//server/share/file",
    r"\\.\C:\file", r"\\?\GLOBALROOT\Device\HarddiskVolume1\file",
    r"\\?\Volume{test}\file", r"\\?\C:relative", r"\\?\1:\file",
    r"\??\C:\file", "https://example.test/file", "file:///C:/file",
    r"\\?\C:\file:stream", "//?/C:/file", r"/\server\file",
])
def test_files_and_voices_reject_nonlocal_or_device_paths_before_io(value, media):
    for module in media:
        with pytest.raises(ValueError):
            module._path_value(value)


def test_resolved_extended_paths_still_check_network_drive(tmp_path, media, monkeypatch):
    from unittest.mock import Mock
    path = extended(tmp_path)
    for module in media:
        drive_type = Mock(return_value=4)
        monkeypatch.setattr(module.ctypes, "WinDLL", Mock(return_value=Mock(GetDriveTypeW=drive_type)))
        with pytest.raises(ValueError, match="网络驱动器"):
            module._check_local_path(path)
        drive_type.assert_called_once_with(tmp_path.anchor)


def test_images_and_emoticons_accept_extended_local_file(tmp_path, media):
    from PIL import Image
    from wxbot_framework.images import validate_image_path
    from wxbot_framework.emoticons import validate_emoticon_path
    gif = tmp_path / "local.gif"
    Image.new("RGB", (2, 2)).save(gif)
    path = extended(gif)
    assert validate_image_path(path) == path
    assert validate_emoticon_path(path) == path


def test_extended_long_local_path_preserves_io_prefix(tmp_path, media):
    files, _ = media
    root = extended(tmp_path / ("a" * 120) / ("b" * 120))
    root.mkdir(parents=True)
    file = root / "long.txt"
    file.write_bytes(b"long path")
    assert len(str(file)) > 260
    assert files.inspect_file(file).path == file
