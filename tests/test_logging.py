"""File logging, rotation, redaction and runtime level switching."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from wechat_onebot import logging_setup
from wechat_onebot.cli import build_parser
from wechat_onebot.config import (
    LOG_LEVELS,
    AppConfig,
    ConfigError,
    DriverConfig,
    LoggingConfig,
    OneBotConfig,
    StorageConfig,
    WebUiConfig,
    default_config,
)


def make_config(tmp_path: Path, **logging_overrides) -> AppConfig:
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper",
                            wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
        webui=WebUiConfig(access_token="webui-token"),
        logging=LoggingConfig(**logging_overrides),
    )


def _minimal_config() -> dict:
    return {
        "onebot": {"access_token": "test"},
        "reverse_ws": [],
        "driver": {"type": "helper", "wechat_account": "wxid_robot", "wechat_pid": 123},
        "storage": {"data_dir": "data", "media_dir": "media"},
        "webui": {"enabled": True, "access_token": "webui"},
    }


@pytest.fixture
def gateway_logger():
    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    old_level = logger.level
    yield logger
    logging_setup._close_file_handler()
    logger.setLevel(old_level)


def test_file_logging_keeps_safe_summaries_and_never_writes_arguments(tmp_path, gateway_logger):
    config = make_config(tmp_path)
    state = logging_setup.configure_logging(config)
    assert state["enabled"] is True and state["level"] == "INFO"
    path = Path(state["file"])
    assert path.is_file()
    logger = logging.getLogger("wechat-onebot.test")
    logger.info("token=SECRET-TOKEN-123 password=hunter2")
    logger.warning("HTTP 上报失败 %s: %s", "SECRET-TOKEN-123",
                   "base64://QUJDREVGR0hJSktMTU5PUA==")
    logger.error("unlisted private content", exc_info=ValueError("secret=abc"))
    logger.debug("OneBot 动作失败: %s", "debug detail")
    text = path.read_text(encoding="utf-8")
    assert "运行事件" in text
    assert "HTTP 上报失败" in text
    assert "运行异常" in text and "ValueError" in text
    for secret in ("SECRET-TOKEN-123", "hunter2", "QUJDREVGR0hJSktMTU5PUA",
                   "unlisted private content", "debug detail", "secret=abc"):
        assert secret not in text
    assert "DEBUG" not in text  # INFO level filters before the handler.
    # No raw record.args structure is ever serialized.
    assert "%s" not in text


def test_hot_switch_level_is_runtime_only_and_validated(tmp_path, gateway_logger):
    config = make_config(tmp_path)
    state = logging_setup.configure_logging(config)
    path = Path(state["file"])
    logger = logging.getLogger("wechat-onebot.hot")
    logger.debug("OneBot 动作失败: %s", "hidden")
    assert logging_setup.current_level(config) == "INFO"
    assert logging_setup.set_log_level("debug") == "DEBUG"
    assert logging_setup.current_level(config) == "DEBUG"
    logger.debug("OneBot 动作失败: %s", "visible")
    text = path.read_text(encoding="utf-8")
    assert "DEBUG" in text and "OneBot 动作失败" in text
    assert "hidden" not in text and "visible" not in text
    # Runtime only: the frozen config value is unchanged and never written.
    assert config.logging.level == "INFO"
    with pytest.raises(ValueError):
        logging_setup.set_log_level("VERBOSE")


def test_rotation_creates_bounded_backups(tmp_path, gateway_logger):
    config = make_config(tmp_path, max_bytes=1024, backup_count=2)
    state = logging_setup.configure_logging(config)
    path = Path(state["file"])
    logger = logging.getLogger("wechat-onebot.rotate")
    for index in range(100):
        logger.warning("HTTP 上报失败 %s: %s", "x" * 40, index)
    files = sorted(path.parent.glob("gateway.log*"))
    assert path.is_file()
    assert (path.parent / "gateway.log.1").is_file()
    assert len(files) <= config.logging.backup_count + 1


def test_file_logging_disabled_by_config(tmp_path, gateway_logger):
    config = make_config(tmp_path, file=False)
    state = logging_setup.configure_logging(config)
    assert state == {"level": "INFO", "file": None, "enabled": False}
    assert not (config.storage.data_dir / "logs").exists()


def test_unwritable_log_target_warns_without_blocking(tmp_path, gateway_logger, monkeypatch, capsys):
    config = make_config(tmp_path)

    class BrokenHandler:
        def __init__(self, *args, **kwargs):
            raise OSError("read-only data directory")

    monkeypatch.setattr(logging_setup, "RotatingFileHandler", BrokenHandler)
    state = logging_setup.configure_logging(config)
    assert state == {"level": "INFO", "file": None, "enabled": False}
    assert "无法启用文件日志" in capsys.readouterr().err


def test_configure_logging_cli_level_override_and_parser_default(tmp_path, gateway_logger):
    config = make_config(tmp_path)
    state = logging_setup.configure_logging(config, "debug")
    assert state["level"] == "DEBUG"
    assert logging_setup.current_level(config) == "DEBUG"
    args = build_parser().parse_args(["run"])
    assert args.log_level is None
    assert build_parser().parse_args(["run", "--log-level", "WARNING"]).log_level == "WARNING"


def test_redact_patterns_and_truncation():
    assert "SECRET" not in logging_setup.redact("access_token=SECRET")
    assert "<redacted>" in logging_setup.redact("access_token=SECRET")
    assert "<redacted>" in logging_setup.redact("Authorization: Bearer abcdefghijkl")
    assert "QUJDREVGR0g" not in logging_setup.redact("payload base64://QUJDREVGR0g=")
    long_text = "x" * (logging_setup.MAX_MESSAGE_CHARS + 10)
    assert logging_setup.redact(long_text).endswith("…<truncated>")


def test_tail_log_file_is_bounded(tmp_path):
    path = tmp_path / "gateway.log"
    assert logging_setup.tail_log_file(path, 5) == ([], False)
    path.write_text("\n".join(f"line-{index}" for index in range(10)) + "\n",
                    encoding="utf-8")
    lines, truncated = logging_setup.tail_log_file(path, 3)
    assert lines == ["line-7", "line-8", "line-9"]
    assert truncated is True
    lines, truncated = logging_setup.tail_log_file(path, 20)
    assert lines == [f"line-{index}" for index in range(10)]
    assert truncated is False
    with pytest.raises(ValueError):
        logging_setup.tail_log_file(path, 0)


def test_webui_log_file_is_fixed_to_the_configured_directory(tmp_path):
    config = make_config(tmp_path)
    path = logging_setup.log_file_path(config)
    assert logging_setup.webui_log_file(config) is None
    path.parent.mkdir(parents=True)
    path.write_text("gateway\n", encoding="utf-8")
    assert logging_setup.webui_log_file(config) == path.resolve()
    custom = make_config(tmp_path, directory=tmp_path / "custom-logs")
    assert logging_setup.webui_log_file(custom) is None
    logging_setup.log_file_path(custom).parent.mkdir(parents=True)
    logging_setup.log_file_path(custom).write_text("custom\n", encoding="utf-8")
    assert logging_setup.webui_log_file(custom) == logging_setup.log_file_path(custom).resolve()


def test_logging_config_defaults_persist_and_validate(tmp_path):
    assert default_config()["logging"] == {
        "file": True, "level": "INFO", "max_bytes": 10 * 1024 * 1024,
        "backup_count": 5, "dir": None,
    }
    assert LOG_LEVELS == ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_minimal_config()), encoding="utf-8")
    config = AppConfig.load(path)
    assert config.logging == LoggingConfig()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["logging"] == default_config()["logging"]
    persisted["logging"] = {
        "file": False, "level": "debug", "max_bytes": 2 * 1024 * 1024,
        "backup_count": 3, "dir": "custom-logs",
    }
    path.write_text(json.dumps(persisted), encoding="utf-8")
    config = AppConfig.load(path)
    assert config.logging.file is False
    assert config.logging.level == "DEBUG"
    assert config.logging.max_bytes == 2 * 1024 * 1024
    assert config.logging.backup_count == 3
    assert config.logging.directory == (tmp_path / "custom-logs").resolve()
    for broken in ({"level": "TRACE"}, {"file": "yes"}, {"max_bytes": 1024},
                   {"backup_count": 0}, {"dir": ""}):
        persisted["logging"] = broken
        path.write_text(json.dumps(persisted), encoding="utf-8")
        with pytest.raises(ConfigError):
            AppConfig.load(path)
