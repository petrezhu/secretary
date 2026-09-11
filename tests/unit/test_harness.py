"""Unit tests for the Agent Harness abstraction layer."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from secretary.harness import (
    BaseHarness,
    HarnessConnectionError,
    HarnessError,
    HarnessRegistry,
    HarnessTimeoutError,
    HermesHarness,
)
from secretary.harness.hermes import _DEFAULT_API_KEY, _DEFAULT_BASE_URL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class DummyHarness(BaseHarness):
    """Minimal concrete implementation for testing BaseHarness."""

    def __init__(self, *, fail_until: int = 0, **kwargs):
        super().__init__(**kwargs)
        self._call_count = 0
        self._fail_until = fail_until

    async def _do_llm_call(self, prompt: str, context: dict[str, Any]) -> str:
        self._call_count += 1
        if self._call_count <= self._fail_until:
            raise RuntimeError("transient failure")
        return f"response to: {prompt}"

    async def _do_execute_tool(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        self._call_count += 1
        if self._call_count <= self._fail_until:
            raise RuntimeError("tool transient failure")
        return {"tool": tool, "executed": True}

    async def _do_get_context(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id}

    async def _do_health_check(self) -> bool:
        return True


def _mock_aiohttp_session(response_json=None, response_status=200):
    """Create a mock aiohttp ClientSession with configurable responses."""
    mock_response = AsyncMock()
    mock_response.status = response_status
    mock_response.json = AsyncMock(return_value=response_json or {})
    mock_response.text = AsyncMock(return_value="error body")

    # Context manager for session.post() / session.get()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_cm)
    mock_session.get = MagicMock(return_value=mock_cm)
    mock_session.closed = False
    mock_session.close = AsyncMock()

    return mock_session, mock_response


# ---------------------------------------------------------------------------
# Tests: Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Verify BaseHarness satisfies AgentHarness Protocol."""

    def test_base_harness_satisfies_protocol(self):
        """BaseHarness subclass should satisfy AgentHarness protocol."""
        harness = DummyHarness()
        # Structural check — if it has all methods, it's compliant
        assert hasattr(harness, "llm_call")
        assert hasattr(harness, "execute_tool")
        assert hasattr(harness, "get_context")
        assert hasattr(harness, "health_check")

    async def test_protocol_methods_are_callable(self):
        harness = DummyHarness()
        result = await harness.llm_call("hello", {})
        assert "hello" in result

        tool_result = await harness.execute_tool("test", {"a": 1})
        assert tool_result["executed"] is True

        ctx = await harness.get_context("sess-1")
        assert ctx["session_id"] == "sess-1"

        ok = await harness.health_check()
        assert ok is True


# ---------------------------------------------------------------------------
# Tests: BaseHarness retry + timeout
# ---------------------------------------------------------------------------


class TestBaseHarnessRetry:
    async def test_succeeds_on_first_try(self):
        harness = DummyHarness()
        result = await harness.llm_call("hi", {})
        assert result == "response to: hi"
        assert harness._call_count == 1

    async def test_succeeds_after_retries(self):
        harness = DummyHarness(fail_until=2, max_retries=3, retry_backoff=0.01)
        result = await harness.llm_call("hi", {})
        assert result == "response to: hi"
        assert harness._call_count == 3

    async def test_raises_after_all_retries_exhausted(self):
        harness = DummyHarness(fail_until=10, max_retries=2, retry_backoff=0.01)
        with pytest.raises(HarnessConnectionError, match="failed after 2 retries"):
            await harness.llm_call("hi", {})

    async def test_timeout_raises_harness_timeout_error(self):
        class SlowHarness(BaseHarness):
            async def _do_llm_call(self, prompt, context):
                await asyncio.sleep(10)
                return "never"

            async def _do_execute_tool(self, tool, params):
                return {}

            async def _do_get_context(self, session_id):
                return {}

            async def _do_health_check(self):
                return True

        harness = SlowHarness(timeout_seconds=0.05, max_retries=1)
        with pytest.raises(HarnessTimeoutError, match="timed out"):
            await harness.llm_call("hi", {})

    async def test_harness_error_not_retried(self):
        class FailingHarness(BaseHarness):
            async def _do_llm_call(self, prompt, context):
                raise HarnessError("permanent")

            async def _do_execute_tool(self, tool, params):
                return {}

            async def _do_get_context(self, session_id):
                return {}

            async def _do_health_check(self):
                return True

        harness = FailingHarness(max_retries=3)
        with pytest.raises(HarnessError, match="permanent"):
            await harness.llm_call("hi", {})


class TestBaseHarnessHealthCheck:
    async def test_health_check_returns_false_on_timeout(self):
        class SlowHealthHarness(BaseHarness):
            async def _do_llm_call(self, prompt, context):
                return ""

            async def _do_execute_tool(self, tool, params):
                return {}

            async def _do_get_context(self, session_id):
                return {}

            async def _do_health_check(self):
                await asyncio.sleep(10)
                return True

        harness = SlowHealthHarness(timeout_seconds=0.05)
        assert await harness.health_check() is False

    async def test_health_check_returns_false_on_exception(self):
        class BrokenHealthHarness(BaseHarness):
            async def _do_llm_call(self, prompt, context):
                return ""

            async def _do_execute_tool(self, tool, params):
                return {}

            async def _do_get_context(self, session_id):
                return {}

            async def _do_health_check(self):
                raise ConnectionError("down")

        harness = BrokenHealthHarness()
        assert await harness.health_check() is False


# ---------------------------------------------------------------------------
# Tests: HermesHarness
# ---------------------------------------------------------------------------


class TestHermesHarnessConstruction:
    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("CLI_PROXY_URL", raising=False)
        monkeypatch.delenv("CLI_PROXY_API_KEY", raising=False)
        h = HermesHarness()
        assert h.base_url == _DEFAULT_BASE_URL.rstrip("/")
        assert h.api_key == _DEFAULT_API_KEY
        assert len(h.model_pool) > 0
        asyncio.get_event_loop().run_until_complete(h.close()) if False else None

    def test_custom_values(self):
        h = HermesHarness(
            base_url="http://custom:9999/v1",
            api_key="sk-custom",
            model_pool=["gpt-4"],
        )
        assert h.base_url == "http://custom:9999/v1"
        assert h.api_key == "sk-custom"
        assert h.model_pool == ["gpt-4"]

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CLI_PROXY_URL", "http://env-host:1234/v1")
        monkeypatch.setenv("CLI_PROXY_API_KEY", "env-key")
        h = HermesHarness()
        assert h.base_url == "http://env-host:1234/v1"
        assert h.api_key == "env-key"


class TestHermesHarnessHealthCheck:
    async def test_health_check_success(self):
        mock_session, mock_resp = _mock_aiohttp_session(response_status=200)
        h = HermesHarness()
        h._session = mock_session

        result = await h.health_check()
        assert result is True
        mock_session.get.assert_called_once()
        await h.close()

    async def test_health_check_failure_status(self):
        mock_session, mock_resp = _mock_aiohttp_session(response_status=503)
        h = HermesHarness()
        h._session = mock_session

        result = await h.health_check()
        assert result is False
        await h.close()

    async def test_health_check_connection_error(self):
        import aiohttp

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("refused"))
        mock_session.close = AsyncMock()

        h = HermesHarness()
        h._session = mock_session

        result = await h.health_check()
        assert result is False
        await h.close()


class TestHermesHarnessLlmCall:
    async def test_llm_call_success_first_model(self):
        response_json = {"choices": [{"message": {"content": "Hello from model"}}]}
        mock_session, mock_resp = _mock_aiohttp_session(
            response_json=response_json, response_status=200
        )
        h = HermesHarness(model_pool=["model-a"])
        h._session = mock_session

        result = await h.llm_call("test prompt", {"system": "you are helpful"})
        assert result == "Hello from model"
        await h.close()

    async def test_llm_call_fallback_to_second_model(self):

        # First call fails, second succeeds
        fail_response = AsyncMock()
        fail_response.status = 500
        fail_response.text = AsyncMock(return_value="server error")
        fail_cm = AsyncMock()
        fail_cm.__aenter__ = AsyncMock(return_value=fail_response)
        fail_cm.__aexit__ = AsyncMock(return_value=False)

        success_response = AsyncMock()
        success_response.status = 200
        success_response.json = AsyncMock(
            return_value={"choices": [{"message": {"content": "fallback ok"}}]}
        )
        success_cm = AsyncMock()
        success_cm.__aenter__ = AsyncMock(return_value=success_response)
        success_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.post = MagicMock(side_effect=[fail_cm, success_cm])
        mock_session.close = AsyncMock()

        h = HermesHarness(model_pool=["bad-model", "good-model"], max_retries=1)
        h._session = mock_session

        result = await h.llm_call("test", {})
        assert result == "fallback ok"
        assert mock_session.post.call_count == 2
        await h.close()

    async def test_llm_call_all_models_fail(self):
        mock_session, mock_resp = _mock_aiohttp_session(response_status=500)
        h = HermesHarness(model_pool=["m1", "m2"], max_retries=1)
        h._session = mock_session

        with pytest.raises(HarnessConnectionError, match="All models in pool exhausted"):
            await h.llm_call("test", {})
        await h.close()

    async def test_llm_call_connection_error(self):
        import aiohttp

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("refused"))
        mock_session.close = AsyncMock()

        h = HermesHarness(model_pool=["m1"], max_retries=1)
        h._session = mock_session

        with pytest.raises(HarnessConnectionError):
            await h.llm_call("test", {})
        await h.close()


class TestHermesHarnessExecuteTool:
    async def test_execute_tool_returns_not_implemented(self):
        h = HermesHarness()
        result = await h.execute_tool("search", {"query": "test"})
        assert result["tool"] == "search"
        assert result["result"] == "not_implemented"
        await h.close()


class TestHermesHarnessGetContext:
    async def test_get_context_returns_session_id(self):
        h = HermesHarness()
        ctx = await h.get_context("sess-123")
        assert ctx["session_id"] == "sess-123"
        assert ctx["history"] == []
        await h.close()


class TestHermesHarnessClose:
    async def test_close_closes_session(self):
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        h = HermesHarness()
        h._session = mock_session
        await h.close()
        mock_session.close.assert_called_once()
        assert h._session is None

    async def test_close_when_already_closed(self):
        mock_session = AsyncMock()
        mock_session.closed = True

        h = HermesHarness()
        h._session = mock_session
        await h.close()
        mock_session.close.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: HarnessRegistry
# ---------------------------------------------------------------------------


class TestHarnessRegistry:
    def test_register_and_get(self):
        registry = HarnessRegistry()
        harness = DummyHarness()
        registry.register("test", harness)
        assert registry.get("test") is harness

    def test_get_nonexistent_returns_none(self):
        registry = HarnessRegistry()
        assert registry.get("nope") is None

    def test_first_registered_becomes_default(self):
        registry = HarnessRegistry()
        h1 = DummyHarness()
        h2 = DummyHarness()
        registry.register("a", h1)
        registry.register("b", h2)
        assert registry.get_default() is h1

    def test_explicit_default(self):
        registry = HarnessRegistry()
        h1 = DummyHarness()
        h2 = DummyHarness()
        registry.register("a", h1)
        registry.register("b", h2, default=True)
        assert registry.get_default() is h2

    def test_set_default(self):
        registry = HarnessRegistry()
        h1 = DummyHarness()
        h2 = DummyHarness()
        registry.register("a", h1)
        registry.register("b", h2)
        registry.set_default("b")
        assert registry.get_default() is h2

    def test_set_default_unknown_raises(self):
        registry = HarnessRegistry()
        with pytest.raises(KeyError, match="not registered"):
            registry.set_default("nope")

    def test_list_names(self):
        registry = HarnessRegistry()
        registry.register("a", DummyHarness())
        registry.register("b", DummyHarness())
        assert set(registry.list_names()) == {"a", "b"}

    def test_remove(self):
        registry = HarnessRegistry()
        registry.register("a", DummyHarness())
        assert registry.remove("a") is True
        assert registry.get("a") is None

    def test_remove_nonexistent(self):
        registry = HarnessRegistry()
        assert registry.remove("nope") is False

    def test_remove_default_falls_back(self):
        registry = HarnessRegistry()
        registry.register("a", DummyHarness())
        registry.register("b", DummyHarness())
        registry.remove("a")
        assert registry.get_default() is not None  # falls back to "b"

    def test_get_default_empty_registry(self):
        registry = HarnessRegistry()
        assert registry.get_default() is None

    async def test_close_all(self):
        registry = HarnessRegistry()
        h1 = DummyHarness()
        h2 = DummyHarness()
        h1.close = AsyncMock()
        h2.close = AsyncMock()
        registry.register("a", h1)
        registry.register("b", h2)
        await registry.close_all()
        h1.close.assert_called_once()
        h2.close.assert_called_once()
