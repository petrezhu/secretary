"""Tests for the secretary CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from secretary.__main__ import (
    _is_process_alive,
    _read_pid,
    _worst_status,
    build_parser,
    cmd_check,
    cmd_status,
    cmd_stop,
    main,
)
from secretary.monitor import CheckResult

# ── Helpers ──────────────────────────────────────────────────────────


def _make_check_result(name: str, status: str, message: str = "") -> CheckResult:
    return CheckResult(name=name, status=status, message=message)  # type: ignore[arg-type]


# ── Parser tests ─────────────────────────────────────────────────────


class TestBuildParser:
    """Test argument parser construction."""

    def test_parser_has_check_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["check"])
        assert args.command == "check"
        assert args.json_output is False

    def test_parser_check_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(["check", "--json"])
        assert args.json_output is True

    def test_parser_check_config_flag(self):
        parser = build_parser()
        args = parser.parse_args(["check", "--config", "/tmp/cfg.yaml"])
        assert args.config == "/tmp/cfg.yaml"

    def test_parser_has_start_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["start"])
        assert args.command == "start"
        assert args.daemon is False

    def test_parser_start_daemon_flag(self):
        parser = build_parser()
        args = parser.parse_args(["start", "--daemon"])
        assert args.daemon is True

    def test_parser_has_status_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_parser_has_stop_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["stop"])
        assert args.command == "stop"

    def test_parser_no_command_returns_none(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None


# ── Helper function tests ────────────────────────────────────────────


class TestWorstStatus:
    """Test _worst_status helper."""

    def test_all_ok(self):
        assert _worst_status(["ok", "ok", "ok"]) == "ok"

    def test_warning_beats_ok(self):
        assert _worst_status(["ok", "warning", "ok"]) == "warning"

    def test_critical_beats_all(self):
        assert _worst_status(["ok", "warning", "critical"]) == "critical"

    def test_empty_list(self):
        assert _worst_status([]) == "ok"

    def test_single_critical(self):
        assert _worst_status(["critical"]) == "critical"


class TestPidHelpers:
    """Test PID file helpers."""

    def test_read_pid_no_file(self, tmp_path):
        with patch("secretary.__main__.PID_FILE", tmp_path / "nonexistent.pid"):
            assert _read_pid() is None

    def test_read_pid_valid(self, tmp_path):
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")
        with patch("secretary.__main__.PID_FILE", pid_file):
            assert _read_pid() == 12345

    def test_read_pid_invalid_content(self, tmp_path):
        pid_file = tmp_path / "bad.pid"
        pid_file.write_text("not_a_number")
        with patch("secretary.__main__.PID_FILE", pid_file):
            assert _read_pid() is None

    def test_is_process_alive_self(self):
        """Current process should be alive."""
        assert _is_process_alive(os.getpid()) is True

    def test_is_process_alive_nonexistent(self):
        """PID 0 should not be alive (or PID -1 which is invalid)."""
        # PID 99999999 is extremely unlikely to exist
        assert _is_process_alive(99999999) is False


# ── cmd_check tests ──────────────────────────────────────────────────


class TestCmdCheck:
    """Test `secretary check` command."""

    def _mock_run_checks(self, results):
        """Patch SecretaryDaemon.run_checks to return given results."""
        with patch("secretary.__main__.SecretaryDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run_checks = AsyncMock(return_value=results)
            MockDaemon.return_value = mock_instance
            yield

    def test_check_human_readable_all_ok(self, capsys):
        """All-ok checks should print ✓ icons and exit 0."""
        results = [
            _make_check_result("health", "ok", "All good"),
            _make_check_result("deadman", "ok", "All tasks running"),
        ]
        with patch("secretary.__main__.SecretaryDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run_checks = AsyncMock(return_value=results)
            MockDaemon.return_value = mock_instance

            parser = build_parser()
            args = parser.parse_args(["check"])
            exit_code = cmd_check(args)

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "✓" in captured.out
        assert "health" in captured.out
        assert "deadman" in captured.out
        assert "Summary: 2 ok" in captured.out

    def test_check_human_readable_warning(self, capsys):
        """Warning results should print ⚠ icon and exit 1."""
        results = [
            _make_check_result("health", "warning", "No weekly goals"),
            _make_check_result("deadman", "ok", "OK"),
        ]
        with patch("secretary.__main__.SecretaryDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run_checks = AsyncMock(return_value=results)
            MockDaemon.return_value = mock_instance

            parser = build_parser()
            args = parser.parse_args(["check"])
            exit_code = cmd_check(args)

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "⚠" in captured.out
        assert "1 ok, 1 warning" in captured.out

    def test_check_human_readable_critical(self, capsys):
        """Critical results should print ✗ icon and exit 2."""
        results = [
            _make_check_result("health", "critical", "DB unreachable"),
        ]
        with patch("secretary.__main__.SecretaryDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run_checks = AsyncMock(return_value=results)
            MockDaemon.return_value = mock_instance

            parser = build_parser()
            args = parser.parse_args(["check"])
            exit_code = cmd_check(args)

        captured = capsys.readouterr()
        assert exit_code == 2
        assert "✗" in captured.out
        assert "critical" in captured.out

    def test_check_json_output(self, capsys):
        """--json flag should produce valid JSON."""
        results = [
            _make_check_result("health", "ok", "All good"),
            _make_check_result("deadman", "warning", "Stale task"),
        ]
        with patch("secretary.__main__.SecretaryDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run_checks = AsyncMock(return_value=results)
            MockDaemon.return_value = mock_instance

            parser = build_parser()
            args = parser.parse_args(["check", "--json"])
            exit_code = cmd_check(args)

        captured = capsys.readouterr()
        assert exit_code == 1  # warning present
        parsed = json.loads(captured.out)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "health"
        assert parsed[0]["status"] == "ok"
        assert parsed[1]["name"] == "deadman"
        assert parsed[1]["status"] == "warning"

    def test_check_json_has_timestamp(self, capsys):
        """JSON output should include timestamp field."""
        results = [_make_check_result("test", "ok", "ok")]
        with patch("secretary.__main__.SecretaryDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run_checks = AsyncMock(return_value=results)
            MockDaemon.return_value = mock_instance

            parser = build_parser()
            args = parser.parse_args(["check", "--json"])
            cmd_check(args)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "timestamp" in parsed[0]


# ── cmd_status tests ─────────────────────────────────────────────────


class TestCmdStatus:
    """Test `secretary status` command."""

    def test_status_no_pid_file(self, tmp_path, capsys):
        """Status with no PID file should report not running."""
        with patch("secretary.__main__.PID_FILE", tmp_path / "no.pid"):
            parser = build_parser()
            args = parser.parse_args(["status"])
            exit_code = cmd_status(args)

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "not running" in captured.out.lower()

    def test_status_running_process(self, tmp_path, capsys):
        """Status with a valid PID file (own PID) should report running."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text(str(os.getpid()))
        with patch("secretary.__main__.PID_FILE", pid_file):
            parser = build_parser()
            args = parser.parse_args(["status"])
            exit_code = cmd_status(args)

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "running" in captured.out.lower()
        assert str(os.getpid()) in captured.out

    def test_status_stale_pid(self, tmp_path, capsys):
        """Status with stale PID should report not running and clean up."""
        pid_file = tmp_path / "stale.pid"
        pid_file.write_text("99999999")
        with patch("secretary.__main__.PID_FILE", pid_file):
            parser = build_parser()
            args = parser.parse_args(["status"])
            exit_code = cmd_status(args)

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "stale" in captured.out.lower()
        # PID file should be cleaned up
        assert not pid_file.exists()


# ── cmd_stop tests ───────────────────────────────────────────────────


class TestCmdStop:
    """Test `secretary stop` command."""

    def test_stop_no_pid_file(self, tmp_path, capsys):
        """Stop with no PID file should report not running."""
        with patch("secretary.__main__.PID_FILE", tmp_path / "no.pid"):
            parser = build_parser()
            args = parser.parse_args(["stop"])
            exit_code = cmd_stop(args)

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "not running" in captured.out.lower()

    def test_stop_stale_pid(self, tmp_path, capsys):
        """Stop with stale PID should clean up and return 1."""
        pid_file = tmp_path / "stale.pid"
        pid_file.write_text("99999999")
        with patch("secretary.__main__.PID_FILE", pid_file):
            parser = build_parser()
            args = parser.parse_args(["stop"])
            exit_code = cmd_stop(args)

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "stale" in captured.out.lower()
        assert not pid_file.exists()


# ── main() dispatcher tests ──────────────────────────────────────────


class TestMain:
    """Test the main() CLI dispatcher."""

    def test_main_no_command(self, capsys):
        """No subcommand should print help and exit 0."""
        with patch("sys.argv", ["secretary"]):
            exit_code = main()
        assert exit_code == 0

    def test_main_stub_commands(self, capsys):
        """Stub commands should print 'not implemented' and exit 0."""
        for cmd in ["morning", "evening", "resource"]:
            with patch("sys.argv", ["secretary", cmd]):
                exit_code = main()
            captured = capsys.readouterr()
            assert exit_code == 0
            assert "not implemented" in captured.out.lower()

    def test_main_check_subcommand(self, capsys):
        """`secretary check` should dispatch to cmd_check."""
        results = [_make_check_result("test", "ok", "fine")]
        with patch("sys.argv", ["secretary", "check"]), \
             patch("secretary.__main__.SecretaryDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run_checks = AsyncMock(return_value=results)
            MockDaemon.return_value = mock_instance
            exit_code = main()

        assert exit_code == 0

    def test_main_status_subcommand(self, capsys):
        """`secretary status` should dispatch to cmd_status."""
        with patch("sys.argv", ["secretary", "status"]), \
             patch("secretary.__main__.PID_FILE", Path("/tmp/__nonexistent_test.pid")):
            exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "not running" in captured.out.lower()
