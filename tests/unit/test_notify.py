"""Unit tests for notification dispatcher and adapters.

All tests use mock adapters — no real messages are ever sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from secretary.config import NotifyConfig
from secretary.notify import (
    Dispatcher,
    HermesQQAdapter,
    Notification,
    NotificationLevel,
    SendResult,
    SMTPAdapter,
    format_conversation,
)

# ── Mock Adapters ────────────────────────────────────────────────────────────


class MockQQAdapter:
    """Mock QQ adapter that records calls without sending anything."""

    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.calls: list[dict[str, Any]] = []

    async def send_text(self, text: str, target: str) -> SendResult:
        self.calls.append({"text": text, "target": target, "method": "send_text"})
        if self.succeed:
            return SendResult(success=True, channel="qq")
        return SendResult(success=False, channel="qq", error="mock failure")

    async def send_html(self, html: str, target: str) -> SendResult:
        self.calls.append({"text": html, "target": target, "method": "send_html"})
        if self.succeed:
            return SendResult(success=True, channel="qq")
        return SendResult(success=False, channel="qq", error="mock failure")

    async def health_check(self) -> bool:
        return self.succeed


class MockEmailAdapter:
    """Mock email adapter that records calls without sending anything."""

    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.calls: list[dict[str, Any]] = []

    async def send_text(self, text: str, target: str) -> SendResult:
        self.calls.append({"text": text, "target": target, "method": "send_text"})
        if self.succeed:
            return SendResult(success=True, channel="email")
        return SendResult(success=False, channel="email", error="mock failure")

    async def send_html(self, html: str, target: str) -> SendResult:
        self.calls.append({"text": html, "target": target, "method": "send_html"})
        if self.succeed:
            return SendResult(success=True, channel="email")
        return SendResult(success=False, channel="email", error="mock failure")

    async def health_check(self) -> bool:
        return self.succeed


class FailingAdapter:
    """Adapter that always fails."""

    async def send_text(self, text: str, target: str) -> SendResult:
        return SendResult(success=False, channel="test", error="always fails")

    async def send_html(self, html: str, target: str) -> SendResult:
        return SendResult(success=False, channel="test", error="always fails")

    async def health_check(self) -> bool:
        return False


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def notify_config() -> NotifyConfig:
    return NotifyConfig()


@pytest.fixture
def dispatcher(notify_config: NotifyConfig) -> Dispatcher:
    return Dispatcher(notify_config)


@pytest.fixture
def mock_qq() -> MockQQAdapter:
    return MockQQAdapter()


@pytest.fixture
def mock_email() -> MockEmailAdapter:
    return MockEmailAdapter()


def _make_notification(
    level: NotificationLevel = NotificationLevel.INFO,
    title: str = "测试通知",
    body: str = "这是一条测试消息",
    channel: str = "qq",
) -> Notification:
    return Notification(level=level, title=title, body=body, channel=channel)


# ── Conversation-style formatting tests ──────────────────────────────────────


class TestFormatConversation:
    """Test the conversation-style message formatting."""

    def test_critical_adds_user_prefix(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SECRETARY_USER_NAME", "主人")
        n = _make_notification(level=NotificationLevel.CRITICAL, body="nginx挂了")
        result = format_conversation(n)
        assert "主人" in result
        assert "nginx挂了" in result

    def test_critical_with_question_mark(self, monkeypatch: pytest.MonkeyPatch):
        """If body already has a question, don't add another."""
        monkeypatch.setenv("SECRETARY_USER_NAME", "主人")
        n = _make_notification(
            level=NotificationLevel.CRITICAL,
            body="数据库连不上了，要重启吗？",
        )
        result = format_conversation(n)
        assert result == "主人，数据库连不上了，要重启吗？"

    def test_critical_without_question_adds_followup(self, monkeypatch: pytest.MonkeyPatch):
        """CRITICAL without question mark gets a follow-up question."""
        monkeypatch.setenv("SECRETARY_USER_NAME", "主人")
        n = _make_notification(level=NotificationLevel.CRITICAL, body="磁盘满了")
        result = format_conversation(n)
        assert "主人" in result
        assert "要我现在处理一下吗？" in result

    def test_warning_adds_zhuyi_prefix(self):
        n = _make_notification(level=NotificationLevel.WARNING, body="内存快满了")
        result = format_conversation(n)
        assert result.startswith("注意一下")
        assert "内存快满了" in result

    def test_info_returns_body_directly(self):
        n = _make_notification(level=NotificationLevel.INFO, body="日报已生成")
        result = format_conversation(n)
        assert result == "日报已生成"

    def test_format_preserves_body_content(self):
        """All levels preserve the original body content."""
        body = "CPU使用率已经超过90%了"
        for level in NotificationLevel:
            n = _make_notification(level=level, body=body)
            result = format_conversation(n)
            assert body in result


# ── Dispatcher tests ─────────────────────────────────────────────────────────


class TestDispatcher:
    """Test the Dispatcher routing logic."""

    @pytest.mark.asyncio
    async def test_send_to_qq(self, dispatcher: Dispatcher, mock_qq: MockQQAdapter):
        dispatcher.register_adapter("qq", mock_qq)
        n = _make_notification(channel="qq")
        result = await dispatcher.send(n)
        assert result.success is True
        assert result.channel == "qq"
        assert len(mock_qq.calls) == 1

    @pytest.mark.asyncio
    async def test_send_to_email(self, dispatcher: Dispatcher, mock_email: MockEmailAdapter):
        dispatcher.register_adapter("email", mock_email)
        n = _make_notification(channel="email")
        result = await dispatcher.send(n)
        assert result.success is True
        assert result.channel == "email"
        assert len(mock_email.calls) == 1

    @pytest.mark.asyncio
    async def test_send_to_both(self, dispatcher: Dispatcher, mock_qq: MockQQAdapter, mock_email: MockEmailAdapter):
        dispatcher.register_adapter("qq", mock_qq)
        dispatcher.register_adapter("email", mock_email)
        n = _make_notification(channel="both")
        result = await dispatcher.send(n)
        assert result.success is True
        # Both adapters should have been called
        assert len(mock_qq.calls) == 1
        assert len(mock_email.calls) == 1

    @pytest.mark.asyncio
    async def test_send_to_both_qq_fails_email_succeeds(
        self, dispatcher: Dispatcher, mock_email: MockEmailAdapter
    ):
        failing_qq = MockQQAdapter(succeed=False)
        dispatcher.register_adapter("qq", failing_qq)
        dispatcher.register_adapter("email", mock_email)
        n = _make_notification(channel="both")
        result = await dispatcher.send(n)
        # Email succeeds, so overall success
        assert result.success is True
        assert result.channel == "email"

    @pytest.mark.asyncio
    async def test_send_to_both_all_fail(self, dispatcher: Dispatcher):
        dispatcher.register_adapter("qq", MockQQAdapter(succeed=False))
        dispatcher.register_adapter("email", MockEmailAdapter(succeed=False))
        n = _make_notification(channel="both")
        result = await dispatcher.send(n)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_adapter_registered(self, dispatcher: Dispatcher):
        n = _make_notification(channel="qq")
        result = await dispatcher.send(n)
        assert result.success is False
        assert "No adapter" in result.error

    @pytest.mark.asyncio
    async def test_adapter_receives_formatted_text(self, dispatcher: Dispatcher, mock_qq: MockQQAdapter, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SECRETARY_USER_NAME", "主人")
        dispatcher.register_adapter("qq", mock_qq)
        n = _make_notification(level=NotificationLevel.CRITICAL, body="服务挂了", channel="qq")
        await dispatcher.send(n)
        sent_text = mock_qq.calls[0]["text"]
        assert "主人" in sent_text
        assert "服务挂了" in sent_text

    @pytest.mark.asyncio
    async def test_health_check(self, dispatcher: Dispatcher, mock_qq: MockQQAdapter, mock_email: MockEmailAdapter):
        dispatcher.register_adapter("qq", mock_qq)
        dispatcher.register_adapter("email", mock_email)
        results = await dispatcher.health_check()
        assert results == {"qq": True, "email": True}

    @pytest.mark.asyncio
    async def test_health_check_partial_failure(self, dispatcher: Dispatcher):
        dispatcher.register_adapter("qq", MockQQAdapter(succeed=True))
        dispatcher.register_adapter("email", MockQQAdapter(succeed=False))
        results = await dispatcher.health_check()
        assert results["qq"] is True
        assert results["email"] is False


# ── Notification model tests ─────────────────────────────────────────────────


class TestNotification:
    """Test the Notification dataclass."""

    def test_default_channel_is_qq(self):
        n = Notification(level=NotificationLevel.INFO, title="t", body="b")
        assert n.channel == "qq"

    def test_default_context_is_empty(self):
        n = Notification(level=NotificationLevel.INFO, title="t", body="b")
        assert n.context == {}

    def test_all_levels(self):
        for level in NotificationLevel:
            n = Notification(level=level, title="t", body="b")
            assert n.level == level

    def test_channel_literal_types(self):
        for ch in ("qq", "email", "both"):
            n = Notification(level=NotificationLevel.INFO, title="t", body="b", channel=ch)
            assert n.channel == ch


class TestSendResult:
    """Test the SendResult dataclass."""

    def test_success_result(self):
        r = SendResult(success=True, channel="qq")
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        r = SendResult(success=False, channel="email", error="timeout")
        assert r.success is False
        assert r.error == "timeout"


# ── Adapter construction tests ───────────────────────────────────────────────


class TestHermesQQAdapterConstruction:
    """Test HermesQQAdapter can be constructed (no actual sends)."""

    def test_default_config(self):
        adapter = HermesQQAdapter()
        assert adapter.gateway_url == "http://127.0.0.1:18789"
        assert adapter.target == "qqbot"
        assert adapter.use_cli is True

    def test_custom_config(self):
        adapter = HermesQQAdapter(
            gateway_url="http://example.com:9999/",
            target="group_123",
            use_cli=True,
        )
        assert adapter.gateway_url == "http://example.com:9999"
        assert adapter.target == "group_123"
        assert adapter.use_cli is True


class TestSMTPAdapterConstruction:
    """Test SMTPAdapter can be constructed (no actual sends)."""

    def test_default_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("SMTP_TO", raising=False)
        adapter = SMTPAdapter()
        assert adapter.host == "smtp.qq.com"
        assert adapter.port == 465
        assert adapter.user == ""
        assert adapter.to_addr == ""

    def test_custom_config(self):
        adapter = SMTPAdapter(
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
        adapter = SMTPAdapter()
        assert adapter.password == "env_secret"


# ── Integration-style test with mock adapters ────────────────────────────────


class TestDispatcherIntegration:
    """End-to-end tests using mock adapters (no real sends)."""

    @pytest.mark.asyncio
    async def test_critical_qq_notification(self, monkeypatch: pytest.MonkeyPatch):
        """CRITICAL notification on QQ gets conversation-style formatting."""
        monkeypatch.setenv("SECRETARY_USER_NAME", "主人")
        config = NotifyConfig()
        dispatcher = Dispatcher(config)
        mock = MockQQAdapter()
        dispatcher.register_adapter("qq", mock)

        n = Notification(
            level=NotificationLevel.CRITICAL,
            title="nginx down",
            body="nginx进程挂了",
            channel="qq",
        )
        result = await dispatcher.send(n)

        assert result.success is True
        assert len(mock.calls) == 1
        sent = mock.calls[0]["text"]
        assert "主人" in sent
        assert "nginx进程挂了" in sent

    @pytest.mark.asyncio
    async def test_warning_email_notification(self):
        """WARNING notification via email."""
        config = NotifyConfig()
        dispatcher = Dispatcher(config)
        mock = MockEmailAdapter()
        dispatcher.register_adapter("email", mock)

        n = Notification(
            level=NotificationLevel.WARNING,
            title="memory high",
            body="内存使用率超过85%",
            channel="email",
        )
        result = await dispatcher.send(n)

        assert result.success is True
        assert len(mock.calls) == 1
        sent = mock.calls[0]["text"]
        assert "注意一下" in sent
        assert "内存使用率超过85%" in sent

    @pytest.mark.asyncio
    async def test_info_both_channels(self):
        """INFO on 'both' sends to QQ and email."""
        config = NotifyConfig()
        dispatcher = Dispatcher(config)
        qq_mock = MockQQAdapter()
        email_mock = MockEmailAdapter()
        dispatcher.register_adapter("qq", qq_mock)
        dispatcher.register_adapter("email", email_mock)

        n = Notification(
            level=NotificationLevel.INFO,
            title="daily report",
            body="日报已经生成好了",
            channel="both",
        )
        result = await dispatcher.send(n)

        assert result.success is True
        assert len(qq_mock.calls) == 1
        assert len(email_mock.calls) == 1
        # INFO level: body is returned directly
        assert qq_mock.calls[0]["text"] == "日报已经生成好了"
