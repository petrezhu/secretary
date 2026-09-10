"""Unit tests for the message gateway.

All tests use mock adapters — no real messages are sent, no real HTTP
connections are made.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from secretary.gateway.adapters import AdapterRegistry, EmailAdapter, QQBotAdapter
from secretary.gateway.api import app

# ── Mock Adapters ────────────────────────────────────────────────────────────


class MockQQBotAdapter:
    """Mock QQ Bot adapter for testing."""

    name = "qq"

    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.calls: list[dict[str, Any]] = []

    async def send(self, text: str, target: str) -> dict[str, Any]:
        self.calls.append({"text": text, "target": target})
        if self.succeed:
            return {"success": True, "channel": "qq", "error": None}
        return {"success": False, "channel": "qq", "error": "mock QQ failure"}

    async def health_check(self) -> bool:
        return self.succeed


class MockEmailAdapter:
    """Mock Email adapter for testing."""

    name = "email"

    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.calls: list[dict[str, Any]] = []

    async def send(self, text: str, target: str) -> dict[str, Any]:
        self.calls.append({"text": text, "target": target})
        if self.succeed:
            return {"success": True, "channel": "email", "error": None}
        return {"success": False, "channel": "email", "error": "mock email failure"}

    async def health_check(self) -> bool:
        return self.succeed


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_registry() -> AdapterRegistry:
    """Create a registry with mock QQ and Email adapters."""
    reg = AdapterRegistry()
    reg.register("qq", MockQQBotAdapter())
    reg.register("email", MockEmailAdapter())
    return reg


@pytest.fixture
def mock_registry_failing() -> AdapterRegistry:
    """Create a registry with always-failing adapters."""
    reg = AdapterRegistry()
    reg.register("qq", MockQQBotAdapter(succeed=False))
    reg.register("email", MockEmailAdapter(succeed=False))
    return reg


@pytest.fixture
def client(mock_registry: AdapterRegistry) -> TestClient:
    """Create a FastAPI TestClient with mock adapters injected."""
    # Patch the global registry
    import secretary.gateway.api as api_module
    original = api_module._registry

    api_module._registry = mock_registry
    api_module._started_at = __import__("datetime").datetime(
        2026, 1, 1, tzinfo=__import__("datetime").timezone.utc
    )

    test_client = TestClient(app)
    yield test_client

    # Restore
    api_module._registry = original


@pytest.fixture
def client_failing(mock_registry_failing: AdapterRegistry) -> TestClient:
    """Create a TestClient with failing adapters."""
    import secretary.gateway.api as api_module
    original = api_module._registry

    api_module._registry = mock_registry_failing
    api_module._started_at = __import__("datetime").datetime(
        2026, 1, 1, tzinfo=__import__("datetime").timezone.utc
    )

    test_client = TestClient(app)
    yield test_client

    api_module._registry = original


# ── Health endpoint tests ────────────────────────────────────────────────────


class TestHealthEndpoint:
    """Test GET /api/health."""

    def test_health_returns_ok(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_always_succeeds(self, client_failing: TestClient):
        """Health endpoint succeeds even when adapters are down."""
        resp = client_failing.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── Status endpoint tests ────────────────────────────────────────────────────


class TestStatusEndpoint:
    """Test GET /api/status."""

    def test_status_returns_platforms(self, client: TestClient):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert "started_at" in data
        assert data["uptime_seconds"] >= 0
        assert isinstance(data["platforms"], dict)
        assert "qq" in data["platforms"]
        assert "email" in data["platforms"]
        assert isinstance(data["messages_sent"], int)

    def test_status_platform_health(self, client: TestClient):
        resp = client.get("/api/status")
        data = resp.json()
        # Mock adapters always return True
        assert data["platforms"]["qq"] is True
        assert data["platforms"]["email"] is True

    def test_status_with_failing_adapters(self, client_failing: TestClient):
        resp = client_failing.get("/api/status")
        data = resp.json()
        assert data["platforms"]["qq"] is False
        assert data["platforms"]["email"] is False


# ── Send endpoint tests ─────────────────────────────────────────────────────


class TestSendEndpoint:
    """Test POST /api/send."""

    def test_send_to_qq_success(self, client: TestClient):
        resp = client.post(
            "/api/send",
            json={"text": "你好", "channel": "qq", "target": "12345"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["channel"] == "qq"
        assert data["error"] is None

    def test_send_to_email_success(self, client: TestClient):
        resp = client.post(
            "/api/send",
            json={"text": "通知", "channel": "email", "target": "test@example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["channel"] == "email"

    def test_send_to_both_success(self, client: TestClient):
        resp = client.post(
            "/api/send",
            json={"text": "广播", "channel": "both", "target": "default"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["channel"] in ("qq", "email")

    def test_send_default_channel_is_qq(self, client: TestClient):
        """When channel is omitted, defaults to qq."""
        resp = client.post("/api/send", json={"text": "默认"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["channel"] == "qq"

    def test_send_empty_text_rejected(self, client: TestClient):
        """Empty text should be rejected by validation."""
        resp = client.post("/api/send", json={"text": "", "channel": "qq"})
        assert resp.status_code == 422  # Validation error

    def test_send_invalid_channel_rejected(self, client: TestClient):
        """Invalid channel name should be rejected."""
        resp = client.post("/api/send", json={"text": "test", "channel": "wechat"})
        assert resp.status_code == 422

    def test_send_missing_text_rejected(self, client: TestClient):
        """Missing text field should be rejected."""
        resp = client.post("/api/send", json={"channel": "qq"})
        assert resp.status_code == 422

    def test_send_increments_message_count(self, client: TestClient):
        """Successful sends increment the message counter."""
        # Get initial count
        resp = client.get("/api/status")
        initial_count = resp.json()["messages_sent"]

        # Send a message
        client.post("/api/send", json={"text": "计数测试", "channel": "qq"})

        # Check count increased
        resp = client.get("/api/status")
        assert resp.json()["messages_sent"] == initial_count + 1

    def test_send_failure_returns_error(self, client_failing: TestClient):
        """When adapter fails, error message is returned."""
        resp = client_failing.post("/api/send", json={"text": "失败", "channel": "qq"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"] is not None
        assert "mock QQ failure" in data["error"]


# ── WebSocket tests ─────────────────────────────────────────────────────────


class TestWebSocket:
    """Test the /ws endpoint placeholder."""

    def test_ws_placeholder(self, client: TestClient):
        """WebSocket returns a placeholder info message."""
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
            assert data["type"] == "info"
            assert "not yet implemented" in data["message"]


# ── AdapterRegistry tests ────────────────────────────────────────────────────


class TestAdapterRegistry:
    """Test the AdapterRegistry class directly."""

    def test_register_and_get(self):
        reg = AdapterRegistry()
        adapter = MockQQBotAdapter()
        reg.register("qq", adapter)
        assert reg.get("qq") is adapter
        assert reg.get("email") is None

    def test_available_list(self):
        reg = AdapterRegistry()
        reg.register("qq", MockQQBotAdapter())
        reg.register("email", MockEmailAdapter())
        assert sorted(reg.available) == ["email", "qq"]

    @pytest.mark.asyncio
    async def test_health_all(self):
        reg = AdapterRegistry()
        reg.register("qq", MockQQBotAdapter(succeed=True))
        reg.register("email", MockEmailAdapter(succeed=False))
        results = await reg.health_all()
        assert results == {"qq": True, "email": False}

    @pytest.mark.asyncio
    async def test_send_no_adapter(self):
        reg = AdapterRegistry()
        result = await reg.send("qq", "hello", "123")
        assert result["success"] is False
        assert "No adapter" in result["error"]

    @pytest.mark.asyncio
    async def test_send_both_with_one_success(self):
        reg = AdapterRegistry()
        reg.register("qq", MockQQBotAdapter(succeed=False))
        reg.register("email", MockEmailAdapter(succeed=True))
        result = await reg.send("both", "hello", "default")
        assert result["success"] is True
        assert result["channel"] == "email"

    @pytest.mark.asyncio
    async def test_send_both_all_fail(self):
        reg = AdapterRegistry()
        reg.register("qq", MockQQBotAdapter(succeed=False))
        reg.register("email", MockEmailAdapter(succeed=False))
        result = await reg.send("both", "hello", "default")
        assert result["success"] is False


# ── Adapter construction tests ───────────────────────────────────────────────


class TestQQBotAdapterConstruction:
    """Test QQBotAdapter can be constructed without real connections."""

    def test_default_from_env(self):
        adapter = QQBotAdapter()
        assert adapter.api_base == "https://api.sgroup.qq.com"

    def test_custom_config(self):
        adapter = QQBotAdapter(
            app_id="test_id",
            client_secret="test_secret",
            api_base="https://custom.api.com/",
        )
        assert adapter.app_id == "test_id"
        assert adapter.client_secret == "test_secret"
        assert adapter.api_base == "https://custom.api.com"

    def test_name_attribute(self):
        adapter = QQBotAdapter()
        assert adapter.name == "qq"


class TestEmailAdapterConstruction:
    """Test EmailAdapter can be constructed without real connections."""

    def test_default_config(self):
        adapter = EmailAdapter()
        assert adapter.host == "smtp.qq.com"
        assert adapter.port == 465

    def test_custom_config(self):
        adapter = EmailAdapter(
            host="smtp.gmail.com",
            port=587,
            user="test@gmail.com",
            password="secret",
            to_addr="dest@gmail.com",
        )
        assert adapter.host == "smtp.gmail.com"
        assert adapter.port == 587
        assert adapter.user == "test@gmail.com"
        assert adapter.password == "secret"
        assert adapter.to_addr == "dest@gmail.com"

    def test_password_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SMTP_PASSWORD", "env_secret")
        adapter = EmailAdapter()
        assert adapter.password == "env_secret"

    def test_name_attribute(self):
        adapter = EmailAdapter()
        assert adapter.name == "email"


# ── CLI integration test ─────────────────────────────────────────────────────


class TestServeCommand:
    """Test that 'secretary serve' is registered in the CLI parser."""

    def test_serve_command_exists(self):
        from secretary.__main__ import build_parser

        parser = build_parser()
        # Should not raise for 'serve --help'
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["serve", "--help"])
        assert exc_info.value.code == 0

    def test_serve_default_args(self):
        from secretary.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"
        assert args.host == "0.0.0.0"
        assert args.port == 8900
        assert args.log_level == "info"

    def test_serve_custom_args(self):
        from secretary.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(["serve", "--host", "127.0.0.1", "--port", "9999", "--log-level", "debug"])
        assert args.host == "127.0.0.1"
        assert args.port == 9999
        assert args.log_level == "debug"
