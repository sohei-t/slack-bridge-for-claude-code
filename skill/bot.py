#!/usr/bin/env python3
"""
Slack <-> Claude Code Bridge Bot (Multi-session + Quick Actions)
Receives messages from Slack DM and sends them to Claude Code running in tmux.

Usage:
  Slack DM:
    run tests                   -> Auto-send if 1 session, button picker if multiple
    @worker1 run tests          -> Send directly to worker1 session
    status                      -> Show all session statuses
    status claude               -> Show specific session screen
    sessions / ls               -> List sessions
    m / menu                    -> Quick action menu

Environment variables (~/.config/ai-agents/profiles/default.env or .env):
  SLACK_BOT_TOKEN=xoxb-...     # Bot User OAuth Token
  SLACK_APP_TOKEN=xapp-...     # App-Level Token (Socket Mode)
  SLACK_ALLOWED_USER=U...      # Your Slack User ID (security: only you can use the bot)
  TMUX_SESSION_NAME=claude     # Default session name (default: claude)
"""

import json
import os
import re
import subprocess
import logging
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# --- Configuration ---


def load_env_file(path: Path) -> dict:
    """Load key=value pairs from an env file."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            value = value.split("#")[0].strip() if "#" in value else value.strip()
            env[key.strip()] = value
    return env


def get_config() -> dict:
    """Get configuration from environment variables, falling back to .env files."""
    env_files = [
        Path(".env"),
        Path.home() / ".config/ai-agents/profiles/default.env",
    ]

    file_env = {}
    for f in env_files:
        loaded = load_env_file(f)
        for k, v in loaded.items():
            if k not in file_env:
                file_env[k] = v

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, file_env.get(key, default))

    return {
        "bot_token": get("SLACK_BOT_TOKEN"),
        "app_token": get("SLACK_APP_TOKEN"),
        "allowed_user": get("SLACK_ALLOWED_USER"),
        "tmux_session": get("TMUX_SESSION_NAME", "claude"),
    }


config = get_config()

SLACK_BOT_TOKEN = config["bot_token"]
SLACK_APP_TOKEN = config["app_token"]
SLACK_ALLOWED_USER = config["allowed_user"]
DEFAULT_SESSION = config["tmux_session"]

if not SLACK_BOT_TOKEN:
    raise ValueError("SLACK_BOT_TOKEN is not set")
if not SLACK_APP_TOKEN:
    raise ValueError("SLACK_APP_TOKEN is not set")
if not SLACK_ALLOWED_USER:
    raise ValueError("SLACK_ALLOWED_USER is not set (required for security)")

# --- Logging ---

LOG_DIR = Path.home() / ".claude/slack-bot"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# --- Pending messages (for button selection) ---

pending_messages = {}

# --- tmux operations ---


def tmux_list_sessions() -> list[str]:
    """List running tmux sessions."""
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return [s.strip() for s in result.stdout.splitlines() if s.strip()]


def tmux_session_exists(session: str) -> bool:
    """Check if a specific tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    return result.returncode == 0


def tmux_send(session: str, text: str) -> bool:
    """Send text to a tmux session (types text + presses Enter)."""
    if not tmux_session_exists(session):
        return False
    subprocess.run(
        ["tmux", "send-keys", "-t", session, "-l", text],
    )
    subprocess.run(
        ["tmux", "send-keys", "-t", session, "Enter"],
    )
    return True


def tmux_capture(session: str) -> str:
    """Capture current tmux pane content (last 50 lines)."""
    if not tmux_session_exists(session):
        return "(no session)"
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-l", "50"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or "(empty)"


# --- Message parsing ---


def parse_mention(text: str) -> tuple[str | None, str]:
    """Parse @session_name prefix. '@worker1 run tests' -> ('worker1', 'run tests')"""
    m = re.match(r"^@(\S+)\s+(.*)", text, re.DOTALL)
    if m:
        return m.group(1), m.group(2).strip()
    return None, text


# --- Quick action: send y/n etc. with session auto-detection ---


def quick_send_to_session(session: str, text: str, say):
    """Quick action send with session auto-detection."""
    if session:
        send_to_session(session, text, say)
    else:
        sessions = tmux_list_sessions()
        if len(sessions) == 0:
            say(":x: No tmux sessions found.")
        elif len(sessions) == 1:
            send_to_session(sessions[0], text, say)
        else:
            # Multiple sessions -> button picker
            msg_id = f"quick_{text}_{len(pending_messages)}"
            pending_messages[msg_id] = text
            buttons = [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": name},
                    "action_id": f"send_to_{msg_id}_{name}",
                    "value": json.dumps({"msg_id": msg_id, "session": name}),
                }
                for name in sessions
            ]
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Select target for `{text}`:*"},
                },
                {"type": "actions", "elements": buttons},
            ]
            say(blocks=blocks, text="Select target")


# --- Slack Bot ---

app = App(token=SLACK_BOT_TOKEN)


def is_allowed(user_id: str) -> bool:
    """Check if the user is authorized."""
    return user_id == SLACK_ALLOWED_USER


@app.event("message")
def handle_message(event, say):
    """Handle DM messages."""
    if event.get("bot_id") or event.get("subtype"):
        return

    user = event.get("user", "")
    text = event.get("text", "").strip()
    channel_type = event.get("channel_type", "")

    log.info(f"Message from user: {user}, channel_type: {channel_type}, text: {text[:50]}")

    if channel_type != "im":
        return
    if not is_allowed(user):
        log.warning(f"Unauthorized user: {user}")
        return

    # Strip optional cc: prefix
    prompt = text
    if text.lower().startswith("cc:"):
        prompt = text[3:].strip()

    if not prompt:
        say("Message is empty. Please type an instruction.")
        return

    # --- Special commands ---
    cmd = prompt.lower().strip()

    # status
    if cmd.startswith("status"):
        handle_status(prompt, say)
        return

    # sessions / ls
    if cmd in ("sessions", "ls"):
        sessions = tmux_list_sessions()
        if sessions:
            lines = [f"  - `{s}`" for s in sessions]
            say(f":computer: Active sessions ({len(sessions)}):\n" + "\n".join(lines))
        else:
            say(":x: No tmux sessions found. Start one with `tcc` on your Mac.")
        return

    # menu / m
    if cmd in ("m", "menu"):
        show_menu(say)
        return

    # --- @mention parsing ---
    target_session, prompt = parse_mention(prompt)

    if target_session:
        send_to_session(target_session, prompt, say)
        return

    # --- Session auto-detection ---
    sessions = tmux_list_sessions()

    if len(sessions) == 0:
        say(":x: No tmux sessions found. Run `tcc` on your Mac first.")
        return

    if len(sessions) == 1:
        send_to_session(sessions[0], prompt, say)
        return

    # Multiple sessions -> button picker
    msg_id = f"{user}_{event.get('ts', '')}"
    pending_messages[msg_id] = prompt

    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": f":computer: {name}"},
            "action_id": f"send_to_{msg_id}_{name}",
            "value": json.dumps({"msg_id": msg_id, "session": name}),
        }
        for name in sessions
    ]

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":arrow_right: *Select target session:*\n> {prompt}",
            },
        },
        {"type": "actions", "elements": buttons},
    ]

    say(blocks=blocks, text="Select target session")


def show_menu(say):
    """Show quick action menu with Block Kit buttons."""
    sessions = tmux_list_sessions()

    # Basic action buttons
    quick_buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "y (approve)"},
            "action_id": "quick_y",
            "style": "primary",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "n (deny)"},
            "action_id": "quick_n",
            "style": "danger",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "status"},
            "action_id": "quick_status",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "sessions"},
            "action_id": "quick_sessions",
        },
    ]

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*:zap: Quick Actions*"},
        },
        {"type": "actions", "elements": quick_buttons},
    ]

    # If multiple sessions, show per-session y/n/status buttons
    if len(sessions) > 1:
        for s in sessions:
            session_buttons = [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "y"},
                    "action_id": f"quick_session_y_{s}",
                    "value": s,
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "n"},
                    "action_id": f"quick_session_n_{s}",
                    "value": s,
                    "style": "danger",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "status"},
                    "action_id": f"quick_session_status_{s}",
                    "value": s,
                },
            ]
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":computer: *{s}*"},
            })
            blocks.append({"type": "actions", "elements": session_buttons})

    say(blocks=blocks, text="Quick action menu")


def handle_status(prompt: str, say):
    """Handle status command."""
    parts = prompt.split(maxsplit=1)
    sessions = tmux_list_sessions()

    if len(parts) >= 2:
        target = parts[1].strip()
        if tmux_session_exists(target):
            pane = tmux_capture(target)
            if len(pane) > 2500:
                pane = "...\n" + pane[-2500:]
            say(f":white_check_mark: `{target}` is running\n```\n{pane}\n```")
        else:
            say(f":x: `{target}` not found.")
        return

    if not sessions:
        say(":x: No tmux sessions found. Start one with `tcc` on your Mac.")
        return

    lines = []
    for s in sessions:
        pane = tmux_capture(s)
        last_lines = [l for l in pane.splitlines() if l.strip()]
        last_line = last_lines[-1] if last_lines else "(empty)"
        if len(last_line) > 80:
            last_line = last_line[:80] + "..."
        lines.append(f":white_check_mark: `{s}`: {last_line}")

    say(f":computer: Sessions ({len(sessions)}):\n" + "\n".join(lines))


def send_to_session(session: str, prompt: str, say):
    """Send a message to the specified tmux session."""
    if not tmux_session_exists(session):
        say(f":x: `{session}` not found.")
        return

    if tmux_send(session, prompt):
        log.info(f"Sent to tmux:{session}: {prompt[:80]}")
        say(f":arrow_right: Sent to `{session}`:\n> {prompt}")
    else:
        say(f":x: Failed to send to `{session}`.")


# --- Button handlers ---

# Session selection buttons (when multiple sessions exist)
@app.action(re.compile(r"send_to_.*"))
def handle_session_select(ack, action, say, body):
    """Handle session selection button press."""
    ack()
    user = body.get("user", {}).get("id", "")
    if not is_allowed(user):
        return

    try:
        data = json.loads(action["value"])
    except (json.JSONDecodeError, KeyError):
        say(":x: Error. Please send the message again.")
        return

    msg_id = data.get("msg_id", "")
    session = data.get("session", "")
    prompt = pending_messages.pop(msg_id, None)

    if prompt is None:
        say(":warning: Message expired. Please send it again.")
        return

    send_to_session(session, prompt, say)


# Quick action buttons (menu / notification y/n)
@app.action("quick_y")
def handle_quick_y(ack, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    quick_send_to_session("", "y", say)


@app.action("quick_n")
def handle_quick_n(ack, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    quick_send_to_session("", "n", say)


@app.action("quick_status")
def handle_quick_status(ack, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    handle_status("status", say)


@app.action("quick_sessions")
def handle_quick_sessions(ack, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    sessions = tmux_list_sessions()
    if sessions:
        lines = [f"  - `{s}`" for s in sessions]
        say(f":computer: Active sessions ({len(sessions)}):\n" + "\n".join(lines))
    else:
        say(":x: No tmux sessions found.")


@app.action("quick_menu")
def handle_quick_menu(ack, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    show_menu(say)


# Per-session quick actions (from menu when multiple sessions)
@app.action(re.compile(r"quick_session_y_.*"))
def handle_quick_session_y(ack, action, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    session = action.get("value", "")
    send_to_session(session, "y", say)


@app.action(re.compile(r"quick_session_n_.*"))
def handle_quick_session_n(ack, action, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    session = action.get("value", "")
    send_to_session(session, "n", say)


@app.action(re.compile(r"quick_session_status_.*"))
def handle_quick_session_status(ack, action, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    session = action.get("value", "")
    handle_status(f"status {session}", say)


# Hook-triggered approve/deny buttons (from notification hook scripts)
@app.action("hook_approve")
def handle_hook_approve(ack, action, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    session = action.get("value", "") or DEFAULT_SESSION
    if tmux_send(session, "y"):
        say(f":white_check_mark: Approved `{session}`")
    else:
        say(f":x: Failed to send to `{session}`")


@app.action("hook_deny")
def handle_hook_deny(ack, action, say, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    session = action.get("value", "") or DEFAULT_SESSION
    if tmux_send(session, "n"):
        say(f":no_entry_sign: Denied `{session}`")
    else:
        say(f":x: Failed to send to `{session}`")


# --- Entry point ---


def main():
    log.info(f"Slack Bot starting... (default session: {DEFAULT_SESSION})")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
