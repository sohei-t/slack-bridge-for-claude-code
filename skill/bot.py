#!/usr/bin/env python3
"""
Slack -> Claude Code Bridge Bot (Multi-session Support)

Receives messages via Slack DM and forwards them to Claude Code
running inside tmux sessions.

Usage (Slack DM):
    Run tests                  -> Auto-send if 1 session, button select if multiple
    @worker1 Run tests         -> Send directly to worker1 session
    status                     -> Check status of all sessions
    status claude              -> Check status of specific session
    sessions / ls              -> List all sessions

Environment variables (~/.config/ai-agents/profiles/default.env):
    SLACK_BOT_TOKEN=xoxb-...     # Bot User OAuth Token
    SLACK_APP_TOKEN=xapp-...     # App-Level Token (for Socket Mode)
    SLACK_ALLOWED_USER=U...      # Allowed Slack user ID (self only)
    TMUX_SESSION_NAME=claude     # Default session name (default: claude)

Architecture:
    Config         - Environment variable loading and validation
    TmuxManager    - Tmux session operations (list, send, capture)
    MessageRouter  - Message parsing and command routing
    SlackBot       - Main orchestrator with Slack event/action handlers
"""

from __future__ import annotations

import json
import os
import signal
import sys
import re
import subprocess
import logging
import threading
import time
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_bolt.context.ack import Ack
from slack_bolt.context.respond import Respond
from slack_bolt.context.say import Say


# ===================================================================
# Config
# ===================================================================


class Config:
    """Application configuration loaded from an environment file.

    Reads key-value pairs from the env file, validates that all required
    Slack credentials are present, and configures the logging subsystem.

    Attributes:
        ENV_FILE: Path to the environment variable file.
        SLACK_BOT_TOKEN: Bot User OAuth token for Slack API calls.
        SLACK_APP_TOKEN: App-Level token for Socket Mode connections.
        SLACK_ALLOWED_USER: Slack user ID allowed to interact with the bot.
        DEFAULT_SESSION: Default tmux session name.
        LOG_DIR: Directory for log files.
        PID_FILE: Path to the PID file for duplicate-process prevention.
        LOG_FORMAT: Format string for log messages.
        PANE_CAPTURE_LIMIT: Maximum number of lines to capture from tmux.
        BUTTON_VALUE_MAX_LENGTH: Maximum character length for Slack button values.
        STATUS_PANE_MAX_LENGTH: Maximum character length for status pane output.
        STATUS_LINE_MAX_LENGTH: Maximum character length for a single status line.
    """

    ENV_FILE: Path = Path.home() / ".config/ai-agents/profiles/default.env"
    LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(message)s"
    PANE_CAPTURE_LIMIT: int = 50
    BUTTON_VALUE_MAX_LENGTH: int = 1900
    STATUS_PANE_MAX_LENGTH: int = 2500
    STATUS_LINE_MAX_LENGTH: int = 80

    def __init__(self, env_file: Path | None = None) -> None:
        """Initialize configuration by loading and validating environment variables.

        Args:
            env_file: Optional override for the environment file path.
                      Defaults to ``~/.config/ai-agents/profiles/default.env``.

        Raises:
            FileNotFoundError: If the env file does not exist.
            ValueError: If any required environment variable is missing.
        """
        if env_file is not None:
            self.ENV_FILE = env_file

        env = self._load_env()

        self.SLACK_BOT_TOKEN: str = env.get("SLACK_BOT_TOKEN", "")
        self.SLACK_APP_TOKEN: str = env.get("SLACK_APP_TOKEN", "")
        self.SLACK_ALLOWED_USER: str = env.get("SLACK_ALLOWED_USER", "")
        self.DEFAULT_SESSION: str = env.get("TMUX_SESSION_NAME", "claude")

        self._validate()

        self.LOG_DIR: Path = Path.home() / ".claude/slack-bot"
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.PID_FILE: Path = self.LOG_DIR / "bot.pid"

        self._configure_logging()

    def _load_env(self) -> dict[str, str]:
        """Read the environment file and return a key-value dictionary.

        Returns:
            A dictionary mapping variable names to their string values.

        Raises:
            FileNotFoundError: If ``self.ENV_FILE`` does not exist.
        """
        if not self.ENV_FILE.exists():
            raise FileNotFoundError(f"{self.ENV_FILE} が見つかりません")
        env: dict[str, str] = {}
        for line in self.ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
        return env

    def _validate(self) -> None:
        """Validate that all required environment variables are set.

        Raises:
            ValueError: If any required variable is empty or missing.
        """
        if not self.SLACK_BOT_TOKEN:
            raise ValueError("SLACK_BOT_TOKEN が未設定です")
        if not self.SLACK_APP_TOKEN:
            raise ValueError("SLACK_APP_TOKEN が未設定です")
        if not self.SLACK_ALLOWED_USER:
            raise ValueError("SLACK_ALLOWED_USER が未設定です（セキュリティのため必須）")

    def _configure_logging(self) -> None:
        """Set up file and console logging handlers."""
        logging.basicConfig(
            level=logging.INFO,
            format=self.LOG_FORMAT,
            handlers=[
                logging.FileHandler(self.LOG_DIR / "bot.log"),
                logging.StreamHandler(),
            ],
        )


# ===================================================================
# TmuxManager
# ===================================================================


class TmuxManager:
    """Manages interactions with tmux sessions.

    Provides methods to list, verify, send text to, and capture output
    from tmux sessions via subprocess calls.

    Attributes:
        capture_lines: Number of lines to capture from the tmux pane.
    """

    def __init__(self, capture_lines: int = 50) -> None:
        """Initialize the TmuxManager.

        Args:
            capture_lines: Number of lines to capture from the pane
                           (default: 50).
        """
        self.capture_lines: int = capture_lines

    def list_sessions(self) -> list[str]:
        """Return a list of running tmux session names.

        Returns:
            A list of session name strings. Returns an empty list if
            tmux is not running.
        """
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []
        return [s.strip() for s in result.stdout.splitlines() if s.strip()]

    def session_exists(self, name: str) -> bool:
        """Check whether a tmux session with the given name exists.

        Args:
            name: The session name to check.

        Returns:
            True if the session exists, False otherwise.
        """
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
        )
        return result.returncode == 0

    def send(self, session: str, text: str) -> bool:
        """Send text to a tmux session followed by an Enter keystroke.

        Args:
            session: The target session name.
            text: The text to send.

        Returns:
            True if the text was sent successfully, False if the
            session does not exist.
        """
        if not self.session_exists(session):
            return False
        subprocess.run(
            ["tmux", "send-keys", "-t", session, "-l", text],
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", session, "Enter"],
        )
        return True

    def capture(self, session: str, lines: int | None = None) -> str:
        """Capture the current pane content of a tmux session.

        Args:
            session: The session name to capture.
            lines: Number of lines to capture. Defaults to
                   ``self.capture_lines``.

        Returns:
            The captured pane text. Returns ``"(セッションなし)"`` if
            the session does not exist, or ``"(空)"`` if the pane is empty.
        """
        if not self.session_exists(session):
            return "(セッションなし)"
        capture_count = lines if lines is not None else self.capture_lines
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", "-S", f"-{capture_count}"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() or "(空)"


# ===================================================================
# MessageRouter
# ===================================================================


class MessageRouter:
    """Parses and classifies incoming Slack messages.

    Determines whether a message is a special command (status, sessions/ls),
    a mention-targeted message, or a regular prompt, and provides the
    parsed result.

    Attributes:
        COMMAND_SESSIONS: Set of command strings that trigger session listing.
        COMMAND_STATUS: The command prefix for status queries.
        CC_PREFIX: Optional prefix that is stripped from messages.
    """

    COMMAND_SESSIONS: frozenset[str] = frozenset({"sessions", "ls"})
    COMMAND_STATUS: str = "status"
    CC_PREFIX: str = "cc:"

    _MENTION_RE: re.Pattern[str] = re.compile(r"^@(\S+)\s+(.*)", re.DOTALL)

    def parse_mention(self, text: str) -> tuple[str | None, str]:
        """Extract a leading ``@session`` mention from the text.

        Parses inputs like ``'@worker1 run tests'`` into a session name
        and the remaining message body.

        Args:
            text: The raw message text.

        Returns:
            A tuple of ``(session_name, message_body)``. If no mention
            is found, returns ``(None, original_text)``.
        """
        m = self._MENTION_RE.match(text)
        if m:
            return m.group(1), m.group(2).strip()
        return None, text

    def strip_cc_prefix(self, text: str) -> str:
        """Remove the optional ``cc:`` prefix from a message.

        Args:
            text: The raw message text.

        Returns:
            The text with the ``cc:`` prefix removed and stripped, or the
            original text if the prefix is not present.
        """
        if text.lower().startswith(self.CC_PREFIX):
            return text[len(self.CC_PREFIX):].strip()
        return text

    def is_status_command(self, text: str) -> bool:
        """Check whether the text is a status command.

        Args:
            text: The lowercased, stripped message text.

        Returns:
            True if the text starts with ``'status'``.
        """
        return text.lower().strip().startswith(self.COMMAND_STATUS)

    def is_sessions_command(self, text: str) -> bool:
        """Check whether the text is a session-listing command.

        Args:
            text: The lowercased, stripped message text.

        Returns:
            True if the text matches ``'sessions'`` or ``'ls'``.
        """
        return text.lower().strip() in self.COMMAND_SESSIONS

    def is_valid_command(self, text: str) -> bool:
        """Check whether the text is any recognized special command.

        Args:
            text: The message text to check.

        Returns:
            True if the text is a status or sessions command.
        """
        return self.is_status_command(text) or self.is_sessions_command(text)


# ===================================================================
# SlackBot
# ===================================================================


class SlackBot:
    """Main Slack Bot orchestrator.

    Initializes the Slack Bolt application, registers event and action
    handlers, and manages the bot lifecycle (PID file, signal handling,
    Socket Mode startup).

    Attributes:
        config: The application configuration.
        tmux: The tmux session manager.
        router: The message parser and router.
        app: The Slack Bolt ``App`` instance.
        log: The logger for this bot instance.
    """

    PENDING_APPROVALS_FILE: Path = Path.home() / ".claude/slack-bot/pending_approvals.json"

    def __init__(self, config: Config) -> None:
        """Initialize the SlackBot with the given configuration.

        Args:
            config: A ``Config`` instance with validated settings.
        """
        self.config: Config = config
        self.tmux: TmuxManager = TmuxManager(
            capture_lines=config.PANE_CAPTURE_LIMIT,
        )
        self.router: MessageRouter = MessageRouter()
        self.app: App = App(token=config.SLACK_BOT_TOKEN)
        self.log: logging.Logger = logging.getLogger(__name__)

        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register all Slack event and action handlers on the app."""
        self.app.event("message")(self._handle_message)
        self.app.action(re.compile(r"send_to_.*"))(self._handle_session_select)
        self.app.action("hook_approve")(self._handle_hook_approve)
        self.app.action("hook_deny")(self._handle_hook_deny)

    # --- Authorization ---

    def is_allowed(self, user_id: str) -> bool:
        """Check whether the given user ID is authorized.

        Args:
            user_id: The Slack user ID to check.

        Returns:
            True if the user is the allowed user, False otherwise.
        """
        return user_id == self.config.SLACK_ALLOWED_USER

    # --- Event Handlers ---

    def _handle_message(self, event: dict[str, Any], say: Say) -> None:
        """Handle incoming Slack DM messages and route them appropriately.

        Ignores bot messages and messages with subtypes. Routes special
        commands (status, sessions/ls) to their handlers, and forwards
        regular messages to tmux sessions.

        Args:
            event: The Slack event payload.
            say: Callable to send messages back to Slack.
        """
        if event.get("bot_id") or event.get("subtype"):
            return

        user: str = event.get("user", "")
        text: str = event.get("text", "").strip()
        channel_type: str = event.get("channel_type", "")

        self.log.info(
            f"Message from user: {user}, channel_type: {channel_type}, text: {text[:50]}"
        )

        if channel_type != "im":
            return
        if not self.is_allowed(user):
            self.log.warning(f"Unauthorized user: {user}")
            return

        # Strip optional cc: prefix
        prompt: str = self.router.strip_cc_prefix(text)

        if not prompt:
            say("メッセージが空です。指示を入力してください。")
            return

        # --- Special commands ---
        if self.router.is_status_command(prompt):
            self._handle_status(prompt, say)
            return

        if self.router.is_sessions_command(prompt):
            self._handle_sessions_list(say)
            return

        # --- @mention parsing ---
        target_session, prompt = self.router.parse_mention(prompt)

        if target_session:
            self._send_to_session(target_session, prompt, say)
            return

        # --- Auto-detect session ---
        sessions: list[str] = self.tmux.list_sessions()

        if len(sessions) == 0:
            say(":x: tmux セッションが見つかりません。Mac で `tcc` を実行してください。")
            return

        if len(sessions) == 1:
            self._send_to_session(sessions[0], prompt, say)
            return

        # Multiple sessions -> show button selection
        self._show_session_buttons(sessions, prompt, say)

    def _handle_status(self, prompt: str, say: Say) -> None:
        """Process the ``status`` command and display session state.

        If ``'status <session>'`` is given, shows the capture of that
        specific session. Otherwise, shows a summary of all sessions.

        Args:
            prompt: The user's command string.
            say: Callable to send messages back to Slack.
        """
        parts: list[str] = prompt.split(maxsplit=1)
        sessions: list[str] = self.tmux.list_sessions()

        if len(parts) >= 2:
            target: str = parts[1].strip()
            if self.tmux.session_exists(target):
                pane: str = self.tmux.capture(target)
                if len(pane) > self.config.STATUS_PANE_MAX_LENGTH:
                    pane = "...\n" + pane[-self.config.STATUS_PANE_MAX_LENGTH:]
                say(f":white_check_mark: `{target}` は稼働中\n```\n{pane}\n```")
            else:
                say(f":x: `{target}` が見つかりません。")
            return

        if not sessions:
            say(":x: tmux セッションが見つかりません。`tcc` で起動してください。")
            return

        lines: list[str] = []
        for s in sessions:
            pane = self.tmux.capture(s)
            last_lines: list[str] = [line for line in pane.splitlines() if line.strip()]
            last_line: str = last_lines[-1] if last_lines else "(空)"
            if len(last_line) > self.config.STATUS_LINE_MAX_LENGTH:
                last_line = last_line[:self.config.STATUS_LINE_MAX_LENGTH] + "..."
            lines.append(f":white_check_mark: `{s}`: {last_line}")

        say(f":computer: セッション一覧 ({len(sessions)}個):\n" + "\n".join(lines))

    def _handle_sessions_list(self, say: Say) -> None:
        """List all running tmux sessions.

        Args:
            say: Callable to send messages back to Slack.
        """
        sessions: list[str] = self.tmux.list_sessions()
        if not sessions:
            say(":x: tmux セッションが見つかりません。`tcc` で起動してください。")
            return
        lines: list[str] = [f":computer: *セッション一覧 ({len(sessions)}個)*"]
        for s in sessions:
            lines.append(f"  • `{s}`")
        say("\n".join(lines))

    def _send_to_session(self, session: str, prompt: str, say: Say) -> None:
        """Send a message to the specified tmux session and notify Slack.

        Args:
            session: The target tmux session name.
            prompt: The message to send.
            say: Callable to send messages back to Slack.
        """
        if not self.tmux.session_exists(session):
            say(f":x: `{session}` が見つかりません。")
            return

        if self.tmux.send(session, prompt):
            self.log.info(f"Sent to tmux:{session}: {prompt[:80]}")
            say(f":arrow_right: `{session}` に送信しました:\n> {prompt}")
        else:
            say(f":x: `{session}` への送信に失敗しました。")

    def _show_session_buttons(
        self, sessions: list[str], prompt: str, say: Say,
    ) -> None:
        """Display Slack buttons for selecting a target session.

        Args:
            sessions: List of available session names.
            prompt: The message to embed in button values.
            say: Callable to send messages back to Slack.
        """
        prompt_for_btn: str = prompt[:self.config.BUTTON_VALUE_MAX_LENGTH]

        buttons: list[dict[str, Any]] = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f":computer: {name}"},
                "action_id": f"send_to_{name}",
                "value": json.dumps({"session": name, "prompt": prompt_for_btn}),
            }
            for name in sessions
        ]

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":arrow_right: *送信先を選択してください:*\n> {prompt}",
                },
            },
            {"type": "actions", "elements": buttons},
        ]

        say(blocks=blocks, text="送信先を選択してください")

    # --- Action Handlers ---

    def _handle_session_select(
        self,
        ack: Ack,
        action: dict[str, Any],
        respond: Respond,
        say: Say,
        body: dict[str, Any],
    ) -> None:
        """Handle session selection button presses.

        Extracts the embedded prompt from the button value and sends
        it to the selected tmux session.

        Args:
            ack: Acknowledge function for the Slack request.
            action: The action payload containing the button value.
            respond: Callable to update the original message.
            say: Callable to send messages back to Slack.
            body: The full request body.
        """
        ack()
        user: str = body.get("user", {}).get("id", "")
        if not self.is_allowed(user):
            return

        try:
            data: dict[str, str] = json.loads(action["value"])
        except (json.JSONDecodeError, KeyError):
            respond(
                text=":x: エラーが発生しました。もう一度送信してください。",
                replace_original=True,
            )
            return

        session: str = data.get("session", "")
        prompt: str = data.get("prompt", "")

        if not prompt:
            respond(
                text=":warning: メッセージが空です。もう一度送信してください。",
                replace_original=True,
            )
            return

        if self.tmux.send(session, prompt):
            self.log.info(f"Sent to tmux:{session}: {prompt[:80]}")
            respond(
                text=f":arrow_right: `{session}` に送信: {prompt[:60]}",
                replace_original=True,
            )
        else:
            respond(
                text=f":x: `{session}` への送信に失敗",
                replace_original=True,
            )

    def _handle_hook_approve(
        self,
        ack: Ack,
        action: dict[str, Any],
        respond: Respond,
        body: dict[str, Any],
    ) -> None:
        """Handle the 'approve' button from hook input notifications.

        Sends ``'y'`` to the tmux session to approve the pending action.

        Args:
            ack: Acknowledge function for the Slack request.
            action: The action payload (value contains session name).
            respond: Callable to update the original message.
            body: The full request body.
        """
        ack()
        if not self.is_allowed(body.get("user", {}).get("id", "")):
            return
        session: str = action.get("value", "") or self.config.DEFAULT_SESSION
        self.clear_pending_approvals()
        if self.tmux.send(session, "y"):
            respond(
                text=f":white_check_mark: `{session}` を許可しました",
                replace_original=True,
            )
        else:
            respond(
                text=f":x: `{session}` への送信に失敗しました",
                replace_original=True,
            )

    def _handle_hook_deny(
        self,
        ack: Ack,
        action: dict[str, Any],
        respond: Respond,
        body: dict[str, Any],
    ) -> None:
        """Handle the 'deny' button from hook input notifications.

        Sends ``'n'`` to the tmux session to reject the pending action.

        Args:
            ack: Acknowledge function for the Slack request.
            action: The action payload (value contains session name).
            respond: Callable to update the original message.
            body: The full request body.
        """
        ack()
        if not self.is_allowed(body.get("user", {}).get("id", "")):
            return
        session: str = action.get("value", "") or self.config.DEFAULT_SESSION
        self.clear_pending_approvals()
        if self.tmux.send(session, "n"):
            respond(
                text=f":no_entry_sign: `{session}` を拒否しました",
                replace_original=True,
            )
        else:
            respond(
                text=f":x: `{session}` への送信に失敗しました",
                replace_original=True,
            )

    # --- Pending Approvals ---

    def clear_pending_approvals(self) -> None:
        """Remove the pending approvals file when a Slack button is pressed."""
        self.PENDING_APPROVALS_FILE.unlink(missing_ok=True)

    def _resolve_slack_messages(self, entries: list[dict[str, Any]]) -> None:
        """Update unresolved Slack notifications to show 'locally approved'."""
        import urllib.request
        resolve_text = ":white_check_mark: *ローカルで許可済み*"
        for entry in entries:
            try:
                payload = {
                    "channel": entry["channel"],
                    "ts": entry["ts"],
                    "text": resolve_text,
                    "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": resolve_text}}],
                }
                req = urllib.request.Request(
                    "https://slack.com/api/chat.update",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.config.SLACK_BOT_TOKEN}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                self.log.warning(f"Failed to resolve Slack message: {e}")

    def _poll_pending_approvals(self) -> None:
        """Background thread: monitor tmux pane changes to detect local approvals."""
        self.log.info("Polling thread started for pending approvals")
        while True:
            time.sleep(3)
            if not self.PENDING_APPROVALS_FILE.exists():
                continue
            try:
                pending = json.loads(self.PENDING_APPROVALS_FILE.read_text())
            except Exception:
                continue
            if not pending:
                continue

            resolved = []
            for entry in pending:
                session = entry.get("session", "")
                snapshot = entry.get("pane_snapshot")
                if not session:
                    resolved.append(entry)
                    continue
                result = subprocess.run(
                    ["tmux", "capture-pane", "-t", session, "-p", "-S", "-5"],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    continue
                current = result.stdout.strip()
                if not snapshot:
                    entry["pane_snapshot"] = current
                    try:
                        self.PENDING_APPROVALS_FILE.write_text(json.dumps(pending))
                    except Exception:
                        pass
                    continue
                if current != snapshot:
                    self.log.info(f"Pane change detected for session '{session}', resolving notification")
                    resolved.append(entry)

            if resolved:
                self._resolve_slack_messages(resolved)
                remaining = [e for e in pending if e not in resolved]
                if remaining:
                    self.PENDING_APPROVALS_FILE.write_text(json.dumps(remaining))
                else:
                    self.PENDING_APPROVALS_FILE.unlink(missing_ok=True)

    # --- Lifecycle ---

    def _kill_existing(self) -> None:
        """Terminate an existing bot process identified by the PID file."""
        if not self.config.PID_FILE.exists():
            return
        try:
            old_pid: int = int(self.config.PID_FILE.read_text().strip())
            os.kill(old_pid, signal.SIGTERM)
            self.log.info(f"既存プロセス (PID {old_pid}) を停止しました")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        self.config.PID_FILE.unlink(missing_ok=True)

    def _cleanup(self, _sig: int = 0, _frame: Any = None) -> None:
        """Remove the PID file and exit on termination signals."""
        self.config.PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    def start(self) -> None:
        """Start the Slack Bot in Socket Mode.

        Kills any existing bot process, writes the current PID file,
        registers signal handlers, and starts the Socket Mode handler.
        """
        self._kill_existing()
        self.config.PID_FILE.write_text(str(os.getpid()))
        signal.signal(signal.SIGTERM, self._cleanup)
        signal.signal(signal.SIGINT, self._cleanup)

        self.log.info(
            f"Slack Bot 起動中... (PID {os.getpid()}, "
            f"default session: {self.config.DEFAULT_SESSION})"
        )

        poller = threading.Thread(target=self._poll_pending_approvals, daemon=True)
        poller.start()

        try:
            handler = SocketModeHandler(self.app, self.config.SLACK_APP_TOKEN)
            handler.start()
        finally:
            self.config.PID_FILE.unlink(missing_ok=True)


# ===================================================================
# Backward-compatible module-level API
# ===================================================================
#
# The following globals and free functions preserve backward compatibility
# with code that imports directly from this module (e.g. existing tests,
# hook scripts). They delegate to a module-level singleton of each class.
# ===================================================================

# --- Config singleton (created at import time) ---
ENV_FILE = Config.ENV_FILE

_config = Config()

SLACK_BOT_TOKEN: str = _config.SLACK_BOT_TOKEN
SLACK_APP_TOKEN: str = _config.SLACK_APP_TOKEN
SLACK_ALLOWED_USER: str = _config.SLACK_ALLOWED_USER
DEFAULT_SESSION: str = _config.DEFAULT_SESSION
PID_FILE: Path = _config.PID_FILE

_log_dir: Path = _config.LOG_DIR
log: logging.Logger = logging.getLogger(__name__)

# --- TmuxManager singleton ---
_tmux = TmuxManager(capture_lines=_config.PANE_CAPTURE_LIMIT)


def tmux_list_sessions() -> list[str]:
    """Return running tmux session names (backward-compatible wrapper).

    Returns:
        A list of session name strings.
    """
    return _tmux.list_sessions()


def tmux_session_exists(session: str) -> bool:
    """Check whether a tmux session exists (backward-compatible wrapper).

    Args:
        session: The session name to check.

    Returns:
        True if the session exists.
    """
    return _tmux.session_exists(session)


def tmux_send(session: str, text: str) -> bool:
    """Send text to a tmux session (backward-compatible wrapper).

    Args:
        session: The target session name.
        text: The text to send.

    Returns:
        True if sent successfully.
    """
    return _tmux.send(session, text)


def tmux_capture(session: str) -> str:
    """Capture tmux pane content (backward-compatible wrapper).

    Args:
        session: The session name to capture.

    Returns:
        The captured pane text.
    """
    return _tmux.capture(session)


# --- MessageRouter singleton ---
_router = MessageRouter()


def parse_mention(text: str) -> tuple[str | None, str]:
    """Parse a leading @mention from text (backward-compatible wrapper).

    Args:
        text: The raw message text.

    Returns:
        A tuple of (session_name, message_body).
    """
    return _router.parse_mention(text)


def load_env() -> dict[str, str]:
    """Load environment variables from the env file (backward-compatible wrapper).

    Returns:
        A dictionary of environment variable key-value pairs.

    Raises:
        FileNotFoundError: If the env file does not exist.
    """
    cfg = Config.__new__(Config)
    cfg.ENV_FILE = ENV_FILE
    return cfg._load_env()


# --- SlackBot singleton ---
_bot = SlackBot(_config)
app: App = _bot.app


def is_allowed(user_id: str) -> bool:
    """Check whether a user is authorized (backward-compatible wrapper).

    Args:
        user_id: The Slack user ID.

    Returns:
        True if the user is allowed.
    """
    return _bot.is_allowed(user_id)


def handle_message(event: dict[str, Any], say: Say) -> None:
    """Handle a Slack message event (backward-compatible wrapper).

    Args:
        event: The Slack event payload.
        say: Callable to send messages to Slack.
    """
    _bot._handle_message(event, say)


def handle_status(prompt: str, say: Say) -> None:
    """Handle the status command (backward-compatible wrapper).

    Args:
        prompt: The user's command string.
        say: Callable to send messages to Slack.
    """
    _bot._handle_status(prompt, say)


def send_to_session(session: str, prompt: str, say: Say) -> None:
    """Send a message to a tmux session (backward-compatible wrapper).

    Args:
        session: The target session name.
        prompt: The message to send.
        say: Callable to send messages to Slack.
    """
    _bot._send_to_session(session, prompt, say)


# --- Pending approvals backward-compatible API ---
PENDING_APPROVALS_FILE: Path = SlackBot.PENDING_APPROVALS_FILE


def clear_pending_approvals() -> None:
    """Clear pending approvals (backward-compatible wrapper)."""
    _bot.clear_pending_approvals()


# --- Entry point ---


def main() -> None:
    """Start the Slack Bot in Socket Mode."""
    _bot.start()


if __name__ == "__main__":
    main()
