from __future__ import annotations

import argparse

from wechat_onebot.cli import build_parser


def test_cli_exposes_only_gateway_commands():
    parser = build_parser()
    subcommands = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    assert set(subcommands.choices) == {"run", "bind", "doctor"}