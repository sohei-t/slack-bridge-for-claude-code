"""Tests for the slack-bridge-for-claude-code bot module.

Tests cover the class-based architecture: TmuxManager, MessageRouter,
Config, and SlackBot, as well as backward-compatible module-level
functions. Uses mocked subprocess and Slack API calls.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ---------------------------------------------------------------------------
# We must patch load_env *before* importing bot, because bot.py calls
# Config() and validates env vars at module level.
# ---------------------------------------------------------------------------

_FAKE_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-fake-token",
    "SLACK_APP_TOKEN": "xapp-fake-token",
    "SLACK_ALLOWED_USER": "U_ALLOWED",
    "TMUX_SESSION_NAME": "claude",
}

_ENV_FILE_CONTENT = "\n".join(f"{k}={v}" for k, v in _FAKE_ENV.items())


def _make_completed(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Helper to build a CompletedProcess for mocking subprocess.run."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Import the bot module with env patched
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_env(tmp_path: Any) -> Any:
    """Patch SLACK_ALLOWED_USER so that authorization checks work in tests."""
    env_file = tmp_path / "default.env"
    env_file.write_text(_ENV_FILE_CONTENT)

    import bot.bot as bot_mod
    # Patch both the module-level variable and the config/bot instance
    original_allowed = bot_mod.SLACK_ALLOWED_USER
    original_config_allowed = bot_mod._config.SLACK_ALLOWED_USER
    original_bot_config_allowed = bot_mod._bot.config.SLACK_ALLOWED_USER
    bot_mod.SLACK_ALLOWED_USER = "U_ALLOWED"
    bot_mod._config.SLACK_ALLOWED_USER = "U_ALLOWED"
    bot_mod._bot.config.SLACK_ALLOWED_USER = "U_ALLOWED"
    yield
    bot_mod.SLACK_ALLOWED_USER = original_allowed
    bot_mod._config.SLACK_ALLOWED_USER = original_config_allowed
    bot_mod._bot.config.SLACK_ALLOWED_USER = original_bot_config_allowed


# We must patch Path methods before importing bot.bot, because
# Config() runs at module level and checks ENV_FILE.exists().
from pathlib import Path

_original_exists = Path.exists
_original_read_text = Path.read_text


def _patched_exists(self: Path) -> bool:
    if "default.env" in str(self):
        return True
    return _original_exists(self)


def _patched_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
    if "default.env" in str(self):
        return _ENV_FILE_CONTENT
    return _original_read_text(self, *args, **kwargs)


_mock_auth_response = MagicMock()
_mock_auth_response.data = {"ok": True, "user_id": "U_BOT", "bot_id": "B_BOT"}
_mock_auth_response.__getitem__ = lambda self, key: self.data[key]
_mock_auth_response.get = lambda key, default=None: _mock_auth_response.data.get(key, default)
_mock_auth_response.status_code = 200
_mock_auth_response.__bool__ = lambda self: True

with patch.object(Path, "exists", _patched_exists), \
     patch.object(Path, "read_text", _patched_read_text), \
     patch("slack_sdk.web.client.WebClient.auth_test", return_value=_mock_auth_response):
    import bot.bot as bot_mod


# ===================================================================
# TmuxManager.list_sessions (via module-level tmux_list_sessions)
# ===================================================================

class TestTmuxListSessions:
    """Tests for TmuxManager.list_sessions() via tmux_list_sessions()."""

    @patch("bot.bot.subprocess.run")
    def test_returns_session_names(self, mock_run: MagicMock) -> None:
        """Should return a list of session names when tmux is running."""
        mock_run.return_value = _make_completed(stdout="claude\nworker1\nworker2\n")
        result = bot_mod.tmux_list_sessions()
        assert result == ["claude", "worker1", "worker2"]

    @patch("bot.bot.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run: MagicMock) -> None:
        """Should return an empty list when tmux is not running."""
        mock_run.return_value = _make_completed(returncode=1)
        result = bot_mod.tmux_list_sessions()
        assert result == []

    @patch("bot.bot.subprocess.run")
    def test_strips_whitespace(self, mock_run: MagicMock) -> None:
        """Should strip leading/trailing whitespace from session names."""
        mock_run.return_value = _make_completed(stdout="  claude  \n  worker1  \n")
        result = bot_mod.tmux_list_sessions()
        assert result == ["claude", "worker1"]

    @patch("bot.bot.subprocess.run")
    def test_skips_blank_lines(self, mock_run: MagicMock) -> None:
        """Should skip blank lines in tmux output."""
        mock_run.return_value = _make_completed(stdout="claude\n\n\nworker1\n")
        result = bot_mod.tmux_list_sessions()
        assert result == ["claude", "worker1"]


# ===================================================================
# TmuxManager.session_exists (via module-level tmux_session_exists)
# ===================================================================

class TestTmuxSessionExists:
    """Tests for TmuxManager.session_exists() via tmux_session_exists()."""

    @patch("bot.bot.subprocess.run")
    def test_returns_true_when_exists(self, mock_run: MagicMock) -> None:
        """Should return True when the session exists."""
        mock_run.return_value = _make_completed(returncode=0)
        assert bot_mod.tmux_session_exists("claude") is True

    @patch("bot.bot.subprocess.run")
    def test_returns_false_when_missing(self, mock_run: MagicMock) -> None:
        """Should return False when the session does not exist."""
        mock_run.return_value = _make_completed(returncode=1)
        assert bot_mod.tmux_session_exists("nonexistent") is False


# ===================================================================
# TmuxManager.send (via module-level tmux_send)
# ===================================================================

class TestTmuxSend:
    """Tests for TmuxManager.send() via tmux_send()."""

    @patch("bot.bot.subprocess.run")
    def test_sends_text_and_enter(self, mock_run: MagicMock) -> None:
        """Should call send-keys twice (text + Enter) and return True."""
        mock_run.return_value = _make_completed(returncode=0)
        result = bot_mod.tmux_send("claude", "hello")
        assert result is True
        # has-session + send-keys (text) + send-keys (Enter)
        assert mock_run.call_count == 3

    @patch("bot.bot.subprocess.run")
    def test_returns_false_when_session_missing(self, mock_run: MagicMock) -> None:
        """Should return False if the session does not exist."""
        mock_run.return_value = _make_completed(returncode=1)
        result = bot_mod.tmux_send("nonexistent", "hello")
        assert result is False


# ===================================================================
# TmuxManager.capture (via module-level tmux_capture)
# ===================================================================

class TestTmuxCapture:
    """Tests for TmuxManager.capture() via tmux_capture()."""

    @patch("bot.bot.subprocess.run")
    def test_captures_pane_content(self, mock_run: MagicMock) -> None:
        """Should return the captured pane content."""
        def side_effect(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            cmd = args[0]
            if "has-session" in cmd:
                return _make_completed(returncode=0)
            return _make_completed(stdout="line1\nline2\nline3\n")
        mock_run.side_effect = side_effect
        result = bot_mod.tmux_capture("claude")
        assert "line1" in result

    @patch("bot.bot.subprocess.run")
    def test_returns_placeholder_when_missing(self, mock_run: MagicMock) -> None:
        """Should return a placeholder when the session doesn't exist."""
        mock_run.return_value = _make_completed(returncode=1)
        result = bot_mod.tmux_capture("nonexistent")
        assert result == "(セッションなし)"

    @patch("bot.bot.subprocess.run")
    def test_returns_empty_placeholder(self, mock_run: MagicMock) -> None:
        """Should return '(空)' when the pane is empty."""
        def side_effect(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            cmd = args[0]
            if "has-session" in cmd:
                return _make_completed(returncode=0)
            return _make_completed(stdout="")
        mock_run.side_effect = side_effect
        result = bot_mod.tmux_capture("claude")
        assert result == "(空)"


# ===================================================================
# MessageRouter.parse_mention (via module-level parse_mention)
# ===================================================================

class TestParseMention:
    """Tests for MessageRouter.parse_mention() via parse_mention()."""

    def test_extracts_mention(self) -> None:
        """Should extract session name and message from '@session msg'."""
        session, msg = bot_mod.parse_mention("@worker1 テスト実行して")
        assert session == "worker1"
        assert msg == "テスト実行して"

    def test_no_mention(self) -> None:
        """Should return None and original text when no mention is present."""
        session, msg = bot_mod.parse_mention("テスト実行して")
        assert session is None
        assert msg == "テスト実行して"

    def test_mention_with_multiline(self) -> None:
        """Should handle multiline messages after mention."""
        session, msg = bot_mod.parse_mention("@worker1 line1\nline2")
        assert session == "worker1"
        assert "line1" in msg

    def test_at_only(self) -> None:
        """Should not match when there is only @ with no space after."""
        session, msg = bot_mod.parse_mention("@worker1")
        assert session is None
        assert msg == "@worker1"


# ===================================================================
# SlackBot.is_allowed (via module-level is_allowed)
# ===================================================================

class TestIsAllowed:
    """Tests for SlackBot.is_allowed() via is_allowed()."""

    def test_allowed_user(self) -> None:
        """Should return True for the allowed user."""
        assert bot_mod.is_allowed("U_ALLOWED") is True

    def test_disallowed_user(self) -> None:
        """Should return False for an unauthorized user."""
        assert bot_mod.is_allowed("U_OTHER") is False

    def test_empty_user(self) -> None:
        """Should return False for an empty user ID."""
        assert bot_mod.is_allowed("") is False


# ===================================================================
# Config._load_env (via module-level load_env)
# ===================================================================

class TestLoadEnv:
    """Tests for Config._load_env() via load_env()."""

    def test_parses_key_value(self, tmp_path: Any) -> None:
        """Should correctly parse KEY=VALUE lines."""
        env_file = tmp_path / "test.env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        with patch.object(bot_mod, "ENV_FILE", env_file):
            result = bot_mod.load_env()
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments(self, tmp_path: Any) -> None:
        """Should skip lines starting with #."""
        env_file = tmp_path / "test.env"
        env_file.write_text("# comment\nFOO=bar\n")
        with patch.object(bot_mod, "ENV_FILE", env_file):
            result = bot_mod.load_env()
        assert result == {"FOO": "bar"}

    def test_skips_empty_lines(self, tmp_path: Any) -> None:
        """Should skip blank lines."""
        env_file = tmp_path / "test.env"
        env_file.write_text("\n\nFOO=bar\n\n")
        with patch.object(bot_mod, "ENV_FILE", env_file):
            result = bot_mod.load_env()
        assert result == {"FOO": "bar"}

    def test_raises_on_missing_file(self, tmp_path: Any) -> None:
        """Should raise FileNotFoundError when the file doesn't exist."""
        env_file = tmp_path / "nonexistent.env"
        with patch.object(bot_mod, "ENV_FILE", env_file):
            with pytest.raises(FileNotFoundError):
                bot_mod.load_env()

    def test_handles_value_with_equals(self, tmp_path: Any) -> None:
        """Should handle values containing '=' characters."""
        env_file = tmp_path / "test.env"
        env_file.write_text("URL=https://example.com?a=1&b=2\n")
        with patch.object(bot_mod, "ENV_FILE", env_file):
            result = bot_mod.load_env()
        assert result == {"URL": "https://example.com?a=1&b=2"}


# ===================================================================
# SlackBot._handle_message (unauthorized user)
# ===================================================================

class TestHandleMessageUnauthorized:
    """Tests for handle_message() with unauthorized users."""

    def test_ignores_unauthorized_user(self, mock_say: MagicMock) -> None:
        """Should silently ignore messages from unauthorized users."""
        event = {
            "user": "U_UNAUTHORIZED",
            "text": "テスト実行して",
            "channel_type": "im",
        }
        with patch.object(bot_mod._bot.tmux, "list_sessions") as mock_sessions:
            bot_mod.handle_message(event, mock_say)
            mock_say.assert_not_called()
            mock_sessions.assert_not_called()

    def test_ignores_bot_messages(self, mock_say: MagicMock) -> None:
        """Should ignore events with bot_id set."""
        event = {
            "bot_id": "B123",
            "user": "U_ALLOWED",
            "text": "hello",
            "channel_type": "im",
        }
        bot_mod.handle_message(event, mock_say)
        mock_say.assert_not_called()

    def test_ignores_non_im(self, mock_say: MagicMock) -> None:
        """Should ignore messages that are not DMs."""
        event = {
            "user": "U_ALLOWED",
            "text": "hello",
            "channel_type": "channel",
        }
        bot_mod.handle_message(event, mock_say)
        mock_say.assert_not_called()

    def test_empty_message(self, mock_say: MagicMock) -> None:
        """Should respond with an error when the message is empty after cc: prefix."""
        event = {
            "user": "U_ALLOWED",
            "text": "cc:",
            "channel_type": "im",
        }
        bot_mod.handle_message(event, mock_say)
        mock_say.assert_called_once()
        assert "空" in mock_say.call_args[0][0]


# ===================================================================
# SlackBot._handle_message (authorized, single session)
# ===================================================================

class TestHandleMessageAuthorized:
    """Tests for handle_message() with authorized users."""

    def test_sends_to_single_session(self, mock_say: MagicMock) -> None:
        """Should auto-send to the only available session."""
        event = {
            "user": "U_ALLOWED",
            "text": "テスト実行して",
            "channel_type": "im",
        }
        with patch.object(bot_mod._bot.tmux, "list_sessions", return_value=["claude"]), \
             patch.object(bot_mod._bot.tmux, "session_exists", return_value=True), \
             patch.object(bot_mod._bot.tmux, "send", return_value=True) as mock_send:
            bot_mod.handle_message(event, mock_say)
            mock_send.assert_called_once_with("claude", "テスト実行して")
            mock_say.assert_called_once()
            assert "送信しました" in mock_say.call_args[0][0]

    def test_shows_buttons_for_multiple_sessions(self, mock_say: MagicMock) -> None:
        """Should show session selection buttons when multiple sessions exist."""
        event = {
            "user": "U_ALLOWED",
            "text": "テスト実行して",
            "channel_type": "im",
            "ts": "1234567890.123456",
        }
        with patch.object(bot_mod._bot.tmux, "list_sessions", return_value=["claude", "worker1"]):
            bot_mod.handle_message(event, mock_say)
            mock_say.assert_called_once()
            call_kwargs = mock_say.call_args[1]
            assert "blocks" in call_kwargs

    def test_button_value_contains_prompt(self, mock_say: MagicMock) -> None:
        """Button value should embed the prompt directly (no pending_messages)."""
        event = {
            "user": "U_ALLOWED",
            "text": "テスト実行して",
            "channel_type": "im",
            "ts": "1234567890.123456",
        }
        with patch.object(bot_mod._bot.tmux, "list_sessions", return_value=["claude", "worker1"]):
            bot_mod.handle_message(event, mock_say)
            blocks = mock_say.call_args[1]["blocks"]
            actions = blocks[1]["elements"]
            for btn in actions:
                data = json.loads(btn["value"])
                assert "prompt" in data
                assert data["prompt"] == "テスト実行して"
                assert "session" in data

    def test_sessions_command(self, mock_say: MagicMock) -> None:
        """Should list sessions when 'sessions' command is sent."""
        event = {
            "user": "U_ALLOWED",
            "text": "sessions",
            "channel_type": "im",
        }
        with patch.object(bot_mod._bot.tmux, "list_sessions", return_value=["s1"]):
            bot_mod.handle_message(event, mock_say)
            mock_say.assert_called_once()
            assert "セッション一覧" in mock_say.call_args[0][0]


# ===================================================================
# MessageRouter class direct tests
# ===================================================================

class TestMessageRouter:
    """Tests for the MessageRouter class directly."""

    def setup_method(self) -> None:
        """Create a fresh MessageRouter instance for each test."""
        self.router: bot_mod.MessageRouter = bot_mod.MessageRouter()

    def test_strip_cc_prefix(self) -> None:
        """Should remove cc: prefix and strip whitespace."""
        assert self.router.strip_cc_prefix("cc: hello") == "hello"
        assert self.router.strip_cc_prefix("CC: hello") == "hello"
        assert self.router.strip_cc_prefix("hello") == "hello"

    def test_is_status_command(self) -> None:
        """Should identify status commands."""
        assert self.router.is_status_command("status") is True
        assert self.router.is_status_command("status claude") is True
        assert self.router.is_status_command("STATUS") is True
        assert self.router.is_status_command("hello") is False

    def test_is_sessions_command(self) -> None:
        """Should identify sessions/ls commands."""
        assert self.router.is_sessions_command("sessions") is True
        assert self.router.is_sessions_command("ls") is True
        assert self.router.is_sessions_command("LS") is True
        assert self.router.is_sessions_command("list") is False

    def test_is_valid_command(self) -> None:
        """Should identify any recognized special command."""
        assert self.router.is_valid_command("status") is True
        assert self.router.is_valid_command("sessions") is True
        assert self.router.is_valid_command("ls") is True
        assert self.router.is_valid_command("hello") is False


# ===================================================================
# TmuxManager class direct tests
# ===================================================================

class TestTmuxManagerClass:
    """Tests for the TmuxManager class directly."""

    def setup_method(self) -> None:
        """Create a fresh TmuxManager instance for each test."""
        self.tmux: bot_mod.TmuxManager = bot_mod.TmuxManager(capture_lines=30)

    @patch("bot.bot.subprocess.run")
    def test_list_sessions(self, mock_run: MagicMock) -> None:
        """Should delegate to tmux list-sessions command."""
        mock_run.return_value = _make_completed(stdout="s1\ns2\n")
        result = self.tmux.list_sessions()
        assert result == ["s1", "s2"]

    @patch("bot.bot.subprocess.run")
    def test_session_exists(self, mock_run: MagicMock) -> None:
        """Should delegate to tmux has-session command."""
        mock_run.return_value = _make_completed(returncode=0)
        assert self.tmux.session_exists("s1") is True

    @patch("bot.bot.subprocess.run")
    def test_send(self, mock_run: MagicMock) -> None:
        """Should send text and Enter key to the session."""
        mock_run.return_value = _make_completed(returncode=0)
        assert self.tmux.send("s1", "hello") is True
        assert mock_run.call_count == 3

    @patch("bot.bot.subprocess.run")
    def test_capture_with_custom_lines(self, mock_run: MagicMock) -> None:
        """Should capture with the configured number of lines."""
        def side_effect(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            cmd = args[0]
            if "has-session" in cmd:
                return _make_completed(returncode=0)
            # Verify custom lines parameter is passed
            assert "30" in cmd
            return _make_completed(stdout="content\n")
        mock_run.side_effect = side_effect
        result = self.tmux.capture("s1")
        assert result == "content"


# ===================================================================
# Config class tests
# ===================================================================

class TestConfigClass:
    """Tests for the Config class."""

    def test_config_has_required_attributes(self) -> None:
        """Should have all required configuration attributes."""
        config = bot_mod._config
        assert hasattr(config, "SLACK_BOT_TOKEN")
        assert hasattr(config, "SLACK_APP_TOKEN")
        assert hasattr(config, "SLACK_ALLOWED_USER")
        assert hasattr(config, "DEFAULT_SESSION")
        assert hasattr(config, "LOG_DIR")
        assert hasattr(config, "PID_FILE")

    def test_config_constants(self) -> None:
        """Should have sensible default constants."""
        assert bot_mod.Config.PANE_CAPTURE_LIMIT == 50
        assert bot_mod.Config.BUTTON_VALUE_MAX_LENGTH == 1900
        assert bot_mod.Config.STATUS_PANE_MAX_LENGTH == 2500
        assert bot_mod.Config.STATUS_LINE_MAX_LENGTH == 80
