#!/usr/bin/env python3
"""
Slack → Claude Code Bridge Bot
Receives messages from Slack DM and sends them to Claude Code running in tmux.

Usage:
  Slack DM:
    any text           → Sends text to Claude Code in tmux
    status             → Shows current tmux pane content
    cc: <instruction>  → Same as above (cc: prefix is optional)

Environment variables:
  SLACK_BOT_TOKEN      # Bot User OAuth Token (xoxb-...)
  SLACK_APP_TOKEN      # App-Level Token for Socket Mode (xapp-...)
  SLACK_ALLOWED_USER   # Your Slack User ID (security: only you can use the bot)
  TMUX_SESSION_NAME    # tmux session name (default: claude)

These can be set via environment variables or a .env file.
Supported .env locations (checked in order):
  1. ./.env (current directory)
  2. ~/.config/ai-agents/profiles/default.env
"""

import os
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
            # Strip inline comments (e.g., KEY=value #comment)
            value = value.split("#")[0].strip() if "#" in value else value.strip()
            env[key.strip()] = value
    return env


def get_config() -> dict:
    """Get configuration from environment variables, falling back to .env files."""
    # Load .env files (later values don't override earlier ones)
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
TMUX_SESSION = config["tmux_session"]

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

# --- tmux operations ---


def tmux_session_exists() -> bool:
    """Check if the tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", TMUX_SESSION],
        capture_output=True,
    )
    return result.returncode == 0


def tmux_send(text: str) -> bool:
    """Send text to the tmux session (types text + presses Enter)."""
    if not tmux_session_exists():
        return False
    subprocess.run(
        ["tmux", "send-keys", "-t", TMUX_SESSION, "-l", text],
    )
    subprocess.run(
        ["tmux", "send-keys", "-t", TMUX_SESSION, "Enter"],
    )
    return True


def tmux_capture() -> str:
    """Capture current tmux pane content (last 50 lines)."""
    if not tmux_session_exists():
        return "(no session)"
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", TMUX_SESSION, "-p", "-l", "50"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "(empty)"


# --- Slack Bot ---

app = App(token=SLACK_BOT_TOKEN)


def is_allowed(user_id: str) -> bool:
    """Check if the user is authorized."""
    return user_id == SLACK_ALLOWED_USER


@app.event("message")
def handle_message(event, say):
    """Handle DM messages."""
    # Ignore bot's own messages and subtypes (join notifications, etc.)
    if event.get("bot_id") or event.get("subtype"):
        return

    user = event.get("user", "")
    text = event.get("text", "").strip()
    channel_type = event.get("channel_type", "")

    log.info(f"Message from user: {user}, channel_type: {channel_type}, text: {text[:50]}")

    # Only accept DMs
    if channel_type != "im":
        return

    # Security: only allowed user
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

    # Special command: status
    if prompt.lower() == "status":
        if tmux_session_exists():
            pane = tmux_capture()
            if len(pane) > 2500:
                pane = "...\n" + pane[-2500:]
            say(f":white_check_mark: tmux `{TMUX_SESSION}` is running\n```\n{pane}\n```")
        else:
            say(f":x: tmux `{TMUX_SESSION}` not found. Start it with `tcc` on your Mac.")
        return

    # Send to tmux
    if not tmux_session_exists():
        say(f":x: tmux `{TMUX_SESSION}` not found. Run `tcc` on your Mac first.")
        return

    if tmux_send(prompt):
        log.info(f"Sent to tmux: {prompt[:80]}")
        say(f":arrow_right: Sent:\n> {prompt}")
    else:
        say(":x: Failed to send.")


# --- Entry point ---


def main():
    log.info(f"Slack Bot starting... (tmux session: {TMUX_SESSION})")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
