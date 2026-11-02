from __future__ import annotations

from ..driver.helper import HelperDriver
from ..driver.mock import MockDriver


class HelperBridgeSession(HelperDriver):
    """A single account/PID native bridge session."""


class MockBridgeSession(MockDriver):
    """A deterministic bridge session for tests and protocol development."""

    pass
