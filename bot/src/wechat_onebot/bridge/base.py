from __future__ import annotations

from ..driver.base import Driver as BridgeSession
from ..driver.base import DriverError, DriverUnsupported, EventCallback


class BridgeError(DriverError):
    pass


class BridgeSessionClosed(BridgeError):
    pass


__all__ = ["BridgeSession", "BridgeError", "BridgeSessionClosed", "DriverError", "DriverUnsupported", "EventCallback"]
