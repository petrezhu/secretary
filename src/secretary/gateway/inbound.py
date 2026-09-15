"""Inbound message handler — the ears of Secretary.

Receives messages from the Hermes pre_gateway_dispatch plugin and decides:
  - Handle simple intents directly (greetings, status, quick tasks)
  - Defer complex queries to the Hermes agent

Decision contract:
  {"action": "handle", "reply": "..."}  → Secretary replies, Hermes skips
  {"action": "allow"}                   → normal Hermes agent flow

Intent matching is delegated to the pluggable registry in
secretary.gateway.intents (regex-only, no LLM, fail-open).

Every handled message is appended to a JSONL ledger read by
scripts/agent_context_bridge.py (a Hermes pre_llm_call shell hook) so the
agent stays aware of what Secretary answered on its behalf.

Additionally, every inbound message — regardless of outcome — is traced to
a queries JSONL log (``queries.jsonl``) as the raw material for the
ColdSkill sedimentation pipeline (candidate mining for repeated queries).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from secretary.data.repository import Repository
from secretary.gateway.intents import IntentContext, build_default_registry

logger = logging.getLogger(__name__)

router = APIRouter()

_registry = build_default_registry()

# Lazy-loaded repository (set by daemon at startup)
_repo: Repository | None = None
_config = None


# ── Rotating JSONL writer (Duplicated Code fix: Q1 + Q6) ────────────────────


class _RotatingJsonlWriter:
    """Best-effort JSONL appender with automatic 256 KB rotation.

    Both callers (``_record_handled`` and ``_record_query``) previously
    duplicated identical rotation+append logic. This single class owns it.
    Never raises — downstream callers are fail-open by design.
    """

    def __init__(self, path: Path, max_bytes: int = 256 * 1024) -> None:
        self.path = path
        self.max_bytes = max_bytes

    def append(self, entry: dict) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self.path.unlink()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass


def _data_path(name: str) -> Path:
    """Resolve a data-file path from SECRETARY_DATA_DIR or /tmp fallback."""
    data_dir = os.environ.get("SECRETARY_DATA_DIR", "")
    return Path(os.path.join(data_dir, name)) if data_dir else Path(f"/tmp/{name}")


_LEDGER = _RotatingJsonlWriter(_data_path("secretary_handled.jsonl"))
_QUERIES = _RotatingJsonlWriter(_data_path("queries.jsonl"))


def _record_handled(text: str, reply: str, intent: str) -> None:
    """Append a handled-message entry; best-effort, never raises."""
    _LEDGER.append({"ts": time.time(), "text": text[:80], "reply": reply[:80], "intent": intent})


def _record_query(text: str, outcome: str) -> None:
    """Append an inbound-query trace entry; best-effort, never raises.

    ``outcome`` is one of: "truncated" (over-long early return), the intent
    handler name (high/medium hit), or "allow" (deferred to the agent).
    """
    _QUERIES.append({"ts": time.time(), "text": text[:80], "outcome": outcome})


def set_repository(repo: Repository) -> None:
    """Called by daemon to wire up the data layer."""
    global _repo
    _repo = repo


def set_config(config) -> None:
    """Called by daemon to expose settings to intent handlers."""
    global _config
    _config = config


# ── Request / Response models ────────────────────────────────────────────────


class InboundRequest(BaseModel):
    """Message from Hermes plugin."""
    text: str = Field(..., min_length=1)
    user_id: str = "unknown"
    chat_id: str = "unknown"
    chat_type: str = "dm"
    platform: str = "qqbot"

class InboundResponse(BaseModel):
    """Decision for Hermes."""

    action: str = Field(..., pattern="^(handle|allow)$")
    reply: str | None = None
    replies: list[str] | None = None  # For multi-part messages


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.post("/api/inbound", response_model=InboundResponse)
async def handle_inbound(req: InboundRequest) -> InboundResponse:
    """Process an inbound message and decide whether to handle it."""
    text = req.text.strip()

    # Skip very long messages (likely complex, let agent handle)
    if len(text) > 200:
        logger.debug("Long message (%d chars), deferring to agent", len(text))
        _record_query(text, "truncated")
        return InboundResponse(action="allow")

    ctx = IntentContext(
        text=text,
        user_id=req.user_id,
        chat_id=req.chat_id,
        repo=_repo,
        config=_config,
    )
    reply, intent_name, confidence = await _registry.dispatch(ctx)

    if confidence == "high" and reply:
        # HIGH: data available, reply immediately
        _record_query(text, intent_name or "?")
        _record_handled(text, reply, intent_name or "?")

        # Split long replies into multiple messages
        from secretary.gateway.intents.base import split_reply
        chunks = split_reply(reply)

        if len(chunks) == 1:
            return InboundResponse(action="handle", reply=reply)
        else:
            return InboundResponse(action="handle", replies=chunks)

    if confidence == "medium" and intent_name:
        # MEDIUM: intent matched but data missing
        # Return preliminary reply, then async补答复 via Harness
        preliminary = "收到，我查一下稍后回复你。"
        _record_query(text, intent_name)
        _record_handled(text, preliminary, f"{intent_name}(pending)")

        # Schedule async补答复
        import asyncio
        asyncio.create_task(
            _async_supplement(text, intent_name, req.chat_id)
        )

        return InboundResponse(action="handle", reply=preliminary)

    # LOW: no intent matched, let agent handle
    logger.debug("No intent matched, deferring to agent: %s", text[:60])
    _record_query(text, "allow")
    return InboundResponse(action="allow")


# ── Async supplement via Harness ─────────────────────────────────────────────

async def _async_supplement(text: str, intent_name: str, chat_id: str) -> None:
    """Background task: call Harness to get a real answer and send it.

    Called when intent is MEDIUM (matched but data missing).
    Uses the Hermes CLIProxyAPI to generate a response, then sends it
    via the Secretary gateway's /api/send endpoint.
    """
    import aiohttp

    try:
        # Build a prompt for the Harness
        prompt = (
            f"用户问了一个关于{intent_name}的问题，但当前数据不可用。"
            f"请根据你所知的信息，简洁回答用户的问题。\n\n"
            f"用户原话：{text}\n\n"
            f"要求：简洁、直接、不超过200字。"
        )

        # Call CLIProxyAPI
        api_base = os.environ.get("CLI_PROXY_URL", "http://127.0.0.1:8317/v1")
        api_key = os.environ.get("CLI_PROXY_API_KEY", "")

        if not api_key:
            logger.warning(
                "[Harness] CLI_PROXY_API_KEY not set, skipping supplement for %s",
                intent_name,
            )
            return

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{api_base}/chat/completions",
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if answer:
                        # Send via Secretary's own /api/send
                        async with session.post(
                            "http://127.0.0.1:8901/api/send",
                            json={"text": answer, "channel": "qq", "target": chat_id},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as send_resp:
                            if send_resp.status == 200:
                                logger.info(
                                    "[Harness] Async supplement sent for %s",
                                    intent_name,
                                )
                            else:
                                logger.warning(
                                    "[Harness] Failed to send supplement: %d",
                                    send_resp.status,
                                )
                    else:
                        logger.warning("[Harness] Empty response from CLIProxyAPI")
                else:
                    logger.warning("[Harness] CLIProxyAPI returned %d", resp.status)

    except Exception as e:
        logger.warning("[Harness] Async supplement failed for %s: %s", intent_name, str(e)[:100])
