import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile


def test_release_archive_matches_launcher_asset_and_single_root_rules(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/package_portable.py'
    spec = importlib.util.spec_from_file_location('portable_package', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / 'timestamp-build'
    bundle.mkdir()
    (bundle / 'BUILD_MANIFEST.json').write_text(json.dumps({'version': '0.9.5'}))
    for name in ('python/python.exe', 'src/wechat_onebot/__init__.py', 'webui/index.html', '.hidden'):
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    archive = module.package_portable(bundle, tmp_path / 'dist')
    assert archive.name == 'wechat-onebot-0.9.5-windows-x64.zip'
    with zipfile.ZipFile(archive) as stream:
        names = stream.namelist()
        assert all(n.startswith('wechat-onebot-0.9.5-windows-x64/') for n in names)
        assert not any('\\' in n for n in names)
        assert len(names) == len(set(n.casefold() for n in names))
        assert stream.read('wechat-onebot-0.9.5-windows-x64/.hidden') == b'fixture'
        assert stream.testzip() is None
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert archive.with_name(archive.name + '.sha256').read_bytes() == (
        digest + '  ' + archive.name + '\n').encode('utf-8')
