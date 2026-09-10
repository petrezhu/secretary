"""Secretary Gateway Plugin — pre_gateway_dispatch hook.

Intercepts every inbound QQ message and routes it through Secretary's
/api/inbound endpoint for smart dispatch:

  - Secretary handles simple intents → reply directly, skip agent
  - Secretary defers complex queries → allow normal agent flow
  - Secretary unreachable → fail-open (allow agent)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

import requests as _requests

logger = logging.getLogger(__name__)

# Secretary Gateway URL (default: localhost:8901)
SECRETARY_URL = os.environ.get(
    "SECRETARY_GATEWAY_URL", "http://127.0.0.1:8901"
)
# Timeout for Secretary API call (seconds)
SECRETARY_TIMEOUT = float(os.environ.get("SECRETARY_TIMEOUT", "3"))
# Platforms to intercept (comma-separated). Empty = all platforms.
INTERCEPT_PLATFORMS = os.environ.get("SECRETARY_INTERCEPT_PLATFORMS", "qqbot")


def register(ctx: Any) -> None:
    """Called by Hermes plugin loader at startup."""
    logger.info("[Secretary-Gateway] register() called, registering pre_gateway_dispatch hook...")
    try:
        ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
        logger.info("[Secretary-Gateway] ✅ Hook registered successfully!")
    except Exception as e:
        logger.error("[Secretary-Gateway] ❌ Hook registration FAILED: %s", e)
    logger.info(
        "[Secretary-Gateway] Plugin configured (url=%s, platforms=%s)",
        SECRETARY_URL,
        INTERCEPT_PLATFORMS or "all",
    )


def _on_pre_gateway_dispatch(
    event: Any,
    gateway: Any,
    session_store: Any = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Pre-dispatch hook — ask Secretary if it wants to handle this message.

    MUST be synchronous (hook dispatcher calls cb(**kwargs) directly).

    Returns:
        {"action": "skip"}   — Secretary handled it (already replied)
        {"action": "allow"}  — let normal agent flow proceed
        None                 — same as allow
    """
    # ── Platform filter ──────────────────────────────────────────────
    platform = getattr(getattr(event, "source", None), "platform", None)
    platform_name = platform.value if platform else "unknown"

    if INTERCEPT_PLATFORMS:
        allowed = {p.strip() for p in INTERCEPT_PLATFORMS.split(",")}
        if platform_name not in allowed:
            return None  # not our platform, pass through

    # ── Skip internal/system events ──────────────────────────────────
    if getattr(event, "internal", False):
        return None

    text = (getattr(event, "text", None) or "").strip()

    # ── OCR: if no text but images present, extract text via vision ──
    if not text:
        media_urls = getattr(event, "media_urls", None) or []
        # Also check for attachments with image URLs
        if not media_urls:
            attachments = getattr(event, "attachments", None) or []
            for att in attachments:
                if isinstance(att, dict):
                    url = att.get("url", "")
                    if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
                        media_urls.append(url)

        if media_urls:
            logger.info("[Secretary-Gateway] No text but %d image(s) found, attempting OCR...", len(media_urls))
            ocr_text = _extract_text_from_images(media_urls)
            if ocr_text:
                text = ocr_text
                logger.info("[Secretary-Gateway] OCR extracted %d chars", len(ocr_text))
            else:
                logger.info("[Secretary-Gateway] OCR failed or returned empty, passing to agent")
                return None  # Let agent handle with vision

    if not text:
        return None

    # ── Auto-memorize user message ────────────────────────────────────
    try:
        from secretary.memory import auto_memorize
        auto_memorize(text, source="qqbot")
    except Exception as e:
        logger.debug("[Secretary-Gateway] Memory extraction failed (non-fatal): %s", e)

    source = getattr(event, "source", None)
    user_id = getattr(source, "user_id", None) or "unknown"
    chat_id = getattr(source, "chat_id", None) or "unknown"
    chat_type = getattr(source, "chat_type", None) or "dm"

    # ── Call Secretary /api/inbound (sync) ───────────────────────────
    payload = {
        "text": text,
        "user_id": user_id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "platform": platform_name,
    }

    try:
        resp = _requests.post(
            f"{SECRETARY_URL}/api/inbound",
            json=payload,
            timeout=SECRETARY_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("Secretary /api/inbound returned %d", resp.status_code)
            return {"action": "allow"}
        decision = resp.json()
    except Exception as exc:
        logger.warning(
            "Secretary unreachable (%s) — fail-open to agent: %s",
            exc, text[:80],
        )
        return {"action": "allow"}

    if not decision:
        return {"action": "allow"}

    action = decision.get("action", "allow")

    if action == "handle":
        # Support both single reply and multiple replies
        replies = decision.get("replies")
        reply = decision.get("reply", "")

        if replies:
            # Multi-part message: schedule as a single async task with
            # small delays to guarantee ordering on the receiving end.
            _send_multi_part(gateway, platform, chat_id, replies, user_id, platform_name)
        elif reply:
            # Single message
            _send_reply_sync(gateway, platform, chat_id, reply)
            logger.info(
                "Secretary handled message from %s on %s: %s → %s",
                user_id, platform_name, text[:40], reply[:60],
            )
        return {"action": "skip"}

    # "allow" or unknown → normal agent flow
    return {"action": "allow"}


def _send_multi_part(
    gateway: Any,
    platform: Any,
    chat_id: str,
    replies: list[str],
    user_id: str,
    platform_name: str,
) -> None:
    """Send multiple reply chunks as a single async task with ordering delays.

    Each chunk is sent sequentially with a 0.3s gap to guarantee the
    receiving platform (e.g. QQ) delivers them in order.
    """
    async def _send_all() -> None:
        for i, chunk in enumerate(replies):
            if not chunk:
                continue
            try:
                adapters = getattr(gateway, "adapters", {})
                adapter = adapters.get(platform)
                if adapter is None:
                    logger.warning("No adapter found for platform %s", platform)
                    return
                result = await adapter.send(chat_id, chunk)
                if not getattr(result, "success", False):
                    logger.warning("Send part %d failed: %s", i + 1, getattr(result, "error", "unknown"))
                else:
                    logger.info(
                        "Secretary handled message from %s on %s: part %d/%d",
                        user_id, platform_name, i + 1, len(replies),
                    )
            except Exception as exc:
                logger.error("Failed to send part %d: %s", i + 1, exc)
            # Delay between parts (skip after the last one)
            if i < len(replies) - 1:
                await asyncio.sleep(0.3)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_send_all())
        else:
            loop.run_until_complete(_send_all())
    except Exception as exc:
        logger.error("Failed to schedule multi-part send: %s", exc)


def _send_reply_sync(
    gateway: Any,
    platform: Any,
    chat_id: str,
    reply: str,
) -> None:
    """Send a reply through the gateway's platform adapter (sync wrapper).

    NOTE: the hook is invoked synchronously from async _handle_message, so
    this runs ON the event loop thread. ``run_coroutine_threadsafe(...).result()``
    there would deadlock (blocks the very loop that must run the coroutine),
    so we schedule the send as a fire-and-forget task instead.
    """
    try:
        adapters = getattr(gateway, "adapters", {})
        adapter = adapters.get(platform)
        if adapter is None:
            logger.warning("No adapter found for platform %s", platform)
            return
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # On the loop thread: schedule as background task, log outcome.
            task = loop.create_task(adapter.send(chat_id, reply))

            def _log_send_done(fut: "asyncio.Future") -> None:
                try:
                    result = fut.result()
                    if not getattr(result, "success", False):
                        logger.warning(
                            "Send failed: %s", getattr(result, "error", "unknown")
                        )
                except Exception as exc:
                    logger.error("Failed to send reply via adapter: %s", exc)

            task.add_done_callback(_log_send_done)
        else:
            result = loop.run_until_complete(adapter.send(chat_id, reply))
            if not getattr(result, "success", False):
                logger.warning("Send failed: %s", getattr(result, "error", "unknown"))
    except Exception as exc:
        logger.error("Failed to send reply via adapter: %s", exc)


# ── OCR Integration ─────────────────────────────────────────────────────────

# Import OCR from the dedicated module (no duplication)
try:
    from secretary.gateway.ocr import extract_text_from_images as _extract_text_from_images
except ImportError:
    # Fallback if ocr.py not available (shouldn't happen in production)
    def _extract_text_from_images(image_urls: list[str]) -> str | None:
        logger.warning("[OCR] ocr.py not available, passing images to agent")
        return None
