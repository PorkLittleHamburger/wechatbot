"""Create the versioned ZIP and checksum consumed by Launcher releases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile


def package_portable(bundle: Path, output: Path) -> Path:
    version = json.loads((bundle / 'BUILD_MANIFEST.json').read_text('utf-8'))['version']
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Portable release requires a numeric three-part version')
    name = f'wechat-onebot-{version}-windows-x64'
    archive = output / (name + '.zip')
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                stream.write(path, name + '/' + path.relative_to(bundle).as_posix())
    with archive.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    archive.with_name(archive.name + '.sha256').write_text(
        digest + '  ' + archive.name + '\n', encoding='utf-8', newline='\n')
    return archive


if __name__ == '__main__':
    print(package_portable(Path(sys.argv[1]), Path(sys.argv[2])))
