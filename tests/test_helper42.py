"""Versioned helper contracts; no injection, WeChat calls or message sends."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_service import make_config
from wechat_onebot.config import DriverConfig, default_config
from wechat_onebot.driver.helper import HelperDriver

HELPER = Path(__file__).resolve().parents[1] / 'vendor/helper'


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    monkeypatch.syspath_prepend(str(HELPER / 'native'))


def test_gateway_defaults_match_all_exact_upstream_builds(helper, tmp_path):
    from wxbot_framework.native_profiles import CORE_PROFILES, profile_for_fingerprint
    profiles = json.loads((HELPER / 'native/compatibility.json').read_text('utf-8'))['profiles']
    versions = tuple(item.version for item in CORE_PROFILES)
    assert versions == DriverConfig().supported_versions
    assert tuple(DriverConfig.from_dict({}, base_dir=tmp_path).supported_versions) == versions
    assert default_config()['driver']['supported_versions'] == list(versions)
    for item in profiles:
        assert profile_for_fingerprint(item['size'], item['sha256']).version == item['wechat_version']
        assert profile_for_fingerprint(item['size'], '0' * 64) is None
    # The currently observed 4.2.0.52 core is not an adapted 4.2.0.12 build.
    assert profile_for_fingerprint(204168240,
        'd733489eea0b62af77e1aa61ba645cd941583d2a64fb2d7572da818d285f8d84') is None


@pytest.mark.parametrize('version', ['4.2.0.11', '4.2.0.12'])
def test_42_capabilities_do_not_advertise_unavailable_native_features(helper, tmp_path, version):
    from wxbot_framework.backend import NativeTextBackend
    backend = NativeTextBackend.__new__(NativeTextBackend)
    backend.raw = SimpleNamespace(wechat_version=version)
    driver = HelperDriver(make_config(tmp_path))
    driver._backend = backend
    assert {'text.receive', 'text.send', 'group.text.mention', 'image.send', 'voice.send',
            'file.send', 'group.members.read', 'request.friend', 'forwarded.receive'} <= driver.capabilities
    assert 'contact.read' not in driver.capabilities
    assert 'emoticon.send' not in driver.capabilities
    # New upstream primitives have no gateway OneBot mapping in this update.
    assert 'text.quote.send' not in driver.capabilities
    assert 'video.send' not in driver.capabilities


@pytest.mark.parametrize('kind,key', [(34, 'voice_status'), (43, 'video_status')])
def test_pending_media_is_not_reported_as_a_second_chat_event(tmp_path, kind, key):
    driver = HelperDriver(make_config(tmp_path))
    data = dict(wx_type=kind, sender_id='wxid_peer', chat_id='123@chatroom',
                monitor_only=True, **{key: 'pending'})
    assert driver._convert_raw_data(data) is None
    for state in ('ready', 'failed', 'unavailable', 'skipped'):
        event = driver._convert_raw_data(dict(data, **{key: state}))
        assert event is not None
        assert len(event.segments) == 1
        assert event.segments[0].type == 'text'


def test_portable_copy_excludes_native_build_intermediates(tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[1] / 'scripts/build_portable.py'
    spec = importlib.util.spec_from_file_location('build_portable_test', script)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    source, output = tmp_path / 'source', tmp_path / 'output'
    (source / '.build').mkdir(parents=True)
    (source / '.build/private.exe').write_bytes(b'private')
    for name in ('text_bridge.dll', 'text_bridge.obj', 'text_bridge.exp', 'text_bridge.lib', 'text_bridge.pdb'):
        (source / name).write_bytes(b'fixture')
    build.copy_tree(source, output)
    assert sorted(p.name for p in output.iterdir()) == ['text_bridge.dll']
