"""Gateway layer — user interaction entry point.

Provides a FastAPI-based message gateway that can receive and send messages
independently of Hermes, with direct QQ Bot and Email adapters.
"""

from __future__ import annotations

from secretary.gateway.adapters import (
    AdapterRegistry,
    EmailAdapter,
    GatewayAdapter,
    QQBotAdapter,
)
from secretary.gateway.api import app
from secretary.gateway.server import create_server

__all__ = [
    "AdapterRegistry",
    "EmailAdapter",
    "GatewayAdapter",
    "QQBotAdapter",
    "app",
    "create_server",
]
