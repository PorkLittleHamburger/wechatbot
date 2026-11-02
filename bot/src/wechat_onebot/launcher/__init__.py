"""Compatibility namespace for the launcher's monorepo source location.

Wheels and portable builds include the canonical modules in this package.
Editable source checkouts resolve them from the adjacent launcher project.
"""
from pathlib import Path

_source = Path(__file__).resolve().parents[4] / "launcher/python/wechat_onebot/launcher"
if _source.is_dir():
    __path__.append(str(_source))
