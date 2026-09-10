"""FastAPI application — the message gateway HTTP layer.

Provides:
- POST /api/send  — send a message to a channel (QQ/Email/both)
- GET  /api/health — liveness probe
- GET  /api/status — daemon status + connected platforms
- WS   /ws        — real-time message stream (future placeholder)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from secretary.gateway.adapters import AdapterRegistry, EmailAdapter, QQBotAdapter
from secretary.gateway.inbound import router as inbound_router

logger = logging.getLogger(__name__)

# ── App factory ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Secretary Gateway",
    description="自有消息网关 — send and receive messages via QQ / Email",
    version="0.1.0",
)

# Include inbound message handler
app.include_router(inbound_router)

# Global adapter registry — populated at startup
_registry = AdapterRegistry()
_started_at: datetime | None = None
_message_count: int = 0


def get_registry() -> AdapterRegistry:
    """Return the global adapter registry."""
    return _registry


def _init_adapters() -> None:
    """Register default adapters (QQ Bot + Email)."""
    _registry.register("qq", QQBotAdapter())
    _registry.register("email", EmailAdapter())


# ── Pydantic models ─────────────────────────────────────────────────────────


class SendRequest(BaseModel):
    """Request body for POST /api/send."""

    text: str = Field(..., min_length=1, description="Message content")
    channel: str = Field(
        default="qq",
        description="Target platform: qq, email, or both",
        pattern="^(qq|email|both)$",
    )
    target: str = Field(
        default="default",
        description="Platform-specific target (channel ID, email address, or 'default')",
    )


class SendResponse(BaseModel):
    """Response from POST /api/send."""

    success: bool
    channel: str
    error: str | None = None


class HealthResponse(BaseModel):
    """Response from GET /api/health."""

    status: str = "ok"


class StatusResponse(BaseModel):
    """Response from GET /api/status."""

    running: bool = True
    started_at: str
    uptime_seconds: float
    platforms: dict[str, bool]
    messages_sent: int


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def _on_startup() -> None:
    """Initialize adapters and data layer on app startup."""
    global _started_at
    _init_adapters()
    _started_at = datetime.now(timezone.utc)

    # Wire up the repository for inbound message handling
    try:
        from secretary.config import load_config
        from secretary.data.repository import Repository
        from secretary.gateway.inbound import set_config, set_repository

        config = load_config()
        repo = Repository(config.data)
        await repo.initialize()
        set_repository(repo)
        set_config(config)
        logger.info("Repository initialized for inbound handler")
    except Exception as exc:
        logger.warning("Failed to initialize repository: %s", exc)

    logger.info("Gateway started with adapters: %s", _registry.available)


@app.post("/api/send", response_model=SendResponse)
async def send_message(req: SendRequest) -> SendResponse:
    """Send a message to the specified channel.

    Channels:
        - ``qq``    — send via QQ Bot OpenAPI
        - ``email`` — send via SMTP
        - ``both``  — send to QQ first; fall back to email
    """
    global _message_count
    result = await _registry.send(
        channel=req.channel,
        text=req.text,
        target=req.target,
    )
    if result.get("success"):
        _message_count += 1
    return SendResponse(**result)


@app.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Liveness probe — always returns 200."""
    return HealthResponse(status="ok")


@app.get("/api/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    """Return gateway status and connected platform health."""
    now = datetime.now(timezone.utc)
    uptime = (now - _started_at).total_seconds() if _started_at else 0.0
    platforms = await _registry.health_all()

    return StatusResponse(
        running=True,
        started_at=_started_at.isoformat() if _started_at else "",
        uptime_seconds=round(uptime, 1),
        platforms=platforms,
        messages_sent=_message_count,
    )


# ── WebSocket (future placeholder) ──────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """WebSocket endpoint for real-time message streaming (future).

    Currently echoes back a placeholder message and closes.
    """
    await ws.accept()
    try:
        await ws.send_json({"type": "info", "message": "WebSocket streaming not yet implemented"})
        await ws.close()
    except WebSocketDisconnect:
        pass
