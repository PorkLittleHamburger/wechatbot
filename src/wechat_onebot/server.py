"""Compatibility import for the former monolithic server module."""

from .onebot.network import EventConnection, EventHub, OneBotNetworkManager

__all__ = ["EventConnection", "EventHub", "OneBotNetworkManager"]
