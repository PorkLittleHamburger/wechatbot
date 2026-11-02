from __future__ import annotations

from ..config import AppConfig
from .base import Driver
from .helper import HelperDriver
from .mock import MockDriver


def create_driver(config: AppConfig) -> Driver:
    if config.driver.type == "helper":
        return HelperDriver(config)
    if config.driver.type == "mock":
        return MockDriver()
    raise ValueError(f"不支持的驱动类型: {config.driver.type}")
