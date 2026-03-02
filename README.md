![CI](https://github.com/sohei-t/slack-bridge-for-claude-code/actions/workflows/ci.yml/badge.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Slack](https://img.shields.io/badge/Slack-Socket_Mode-4A154B?logo=slack&logoColor=white)
![tmux](https://img.shields.io/badge/tmux-terminal_multiplexer-1BB91F)

# Slack Bridge for Claude Code

A bidirectional integration tool that bridges [Slack](https://slack.com) and [Claude Code](https://docs.anthropic.com/en/docs/claude-code), letting you control Claude Code from your phone via Slack Bot DM. Receive real-time notifications when tasks complete or when Claude needs permission, and send instructions back -- all without touching your Mac.

---

## The Problem

When Claude Code runs long tasks on your Mac, you have no way to know when it finishes unless you are sitting at your desk. If Claude needs permission to execute a command, it blocks until you physically walk over and type `y`. This tool eliminates that friction entirely.

## The Solution

Slack Bridge creates a two-way communication channel between your phone and Claude Code:

- **Mac to Phone**: Automatic notifications for task completion and permission prompts
- **Phone to Mac**: Send instructions, approve/deny permissions, and check status -- all from Slack DM

---

## Architecture

```
+--------------------------------------------------------------+
|  Your Mac (always-on)                                        |
|                                                              |
|  +-----------------+       +---------------------------+     |
|  | bot.py          |       | tmux sessions             |     |
|  | (Socket Mode)   |------>|  +---------------------+  |     |
|  |                 |       |  | Claude Code (main)  |  |     |
|  | Receives Slack  |       |  +---------------------+  |     |
|  | messages and    |       |  | Claude Code (worker)|  |     |
|  | button actions  |       |  +---------------------+  |     |
|  +-----------------+       +-------------+-------------+     |
|                                          |                   |
|  +-----------------+                     |                   |
|  | Hook Scripts    |<--------------------+                   |
|  |                 |  Claude Code events:                    |
|  | slack-notify.sh |  - Stop (task complete)                 |
|  | slack-notify-   |  - Notification (permission prompt)     |
|  |   waiting.sh    |                                         |
|  +-----------------+                                         |
|          |                                                   |
+----------|---------------------------------------------------+
           | Slack API (HTTPS)
           v
+--------------------------------------------------------------+
|  Slack API (api.slack.com)                                   |
|  - Socket Mode WebSocket (bot.py <-> Slack)                  |
|  - REST API (hook scripts -> Slack)                          |
+--------------------------------------------------------------+
           ^
           |
+--------------------+
|  Phone (Slack App) |
|  - Notifications   |
|  - Approve / Deny  |
|  - Send commands   |
+--------------------+
```

**Data flow summary:**

| Path | Mechanism | Purpose |
|------|-----------|---------|
| Phone -> Mac | Slack Socket Mode -> `bot.py` -> `tmux send-keys` | Send instructions to Claude Code |
| Mac -> Phone | Claude Code hook -> `slack-notify.sh` -> Slack REST API | Task completion notification |
| Mac -> Phone | Claude Code hook -> `slack-notify-waiting.sh` -> Slack REST API | Permission prompt with Approve/Deny buttons |
| Phone -> Mac | Slack Button Action -> `bot.py` -> `tmux send-keys` | One-tap approve (`y`) or deny (`n`) |

---

## Features

### Bidirectional Communication

| Direction | Feature | Details |
|-----------|---------|---------|
| Mac -> Phone | Task completion notification | Claude's response summary + tmux screen capture |
| Mac -> Phone | Permission prompt notification | Prompt details + **Approve / Deny** buttons (Block Kit) |
| Phone -> Mac | Send instructions | Type in Slack DM, delivered to Claude Code via tmux |
| Phone -> Mac | Check status | `status` command shows current tmux screen content |
| Phone -> Mac | Multi-session support | `@session_name` mention or interactive session picker |

### Notifications

- **Task completion**: When Claude finishes a task, you receive a notification containing Claude's response summary and a tmux screen capture. No action buttons -- purely informational.
- **Permission prompt**: When Claude needs approval (e.g., to run a shell command), you receive the prompt details with **Approve** and **Deny** buttons for one-tap response via Slack's Block Kit interactive components.

### Multi-Session Support

Run multiple Claude Code instances in separate tmux sessions and control them all from a single Slack DM:

- **Auto-detection**: If only one tmux session exists, messages route automatically
- **Session picker**: If multiple sessions exist, Block Kit buttons let you choose the target
- **Direct mention**: `@worker1 run tests` sends directly to the `worker1` session
- **Status overview**: `status` shows all sessions with their last output line

### Security

- **Allowed user filtering**: Only the configured Slack user ID can interact with the bot
- **Unauthorized requests are silently ignored**: No information leakage to other users
- **PID-based process management**: Prevents duplicate bot instances

### Hook-Based Integration

The bridge uses Claude Code's native hook system -- no polling, no custom Claude Code modifications:

- **Stop hook** (`slack-notify.sh`): Fires when Claude finishes a task
- **Notification hook** (`slack-notify-waiting.sh`): Fires on permission prompts (matched by `permission_prompt`)

Both hooks run as background processes to avoid blocking Claude Code.

---

## Quick Start

### Prerequisites

- macOS (or Linux with tmux)
- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- A Slack workspace where you can create apps

### Step 1: Install Dependencies

```bash
pip3 install slack_bolt slack_sdk
brew install tmux  # if not already installed
```

### Step 2: Create a Slack App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) -> **Create New App** -> **From scratch**
2. Name it (e.g., `Claude Code Bridge`) and select your workspace
3. Enable **Socket Mode**:
   - Navigate to **Basic Information** -> **App-Level Tokens** -> **Generate Token**
   - Scope: `connections:write`
   - Save the `xapp-...` token
4. Add **Bot Token Scopes** under **OAuth & Permissions**:
   - `chat:write` -- Send messages
   - `im:history` -- Read DM history
   - `im:write` -- Open DM channels
   - `users:read` -- Read user info
5. Enable **Event Subscriptions**:
   - Subscribe to bot event: `message.im`
6. **Install to Workspace**:
   - Save the `xoxb-...` Bot User OAuth Token
7. Find your **Slack User ID**:
   - Click your profile in Slack -> **...** -> **Copy member ID**

### Step 3: Configure Environment Variables

```bash
cp .env.example .env
# Edit .env with your tokens and user ID
```

Or add to `~/.config/ai-agents/profiles/default.env`:

```
SLACK_NOTIFY_ENABLED=true
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
SLACK_ALLOWED_USER=U0000000000
TMUX_SESSION_NAME=claude
```

### Step 4: Deploy Files

```bash
# Bot
mkdir -p ~/.claude/slack-bot
cp bot/bot.py ~/.claude/slack-bot/

# Hook scripts
mkdir -p ~/.claude/hooks
cp hooks/slack-notify.sh ~/.claude/hooks/
cp hooks/slack-notify-waiting.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/slack-notify*.sh
```

### Step 5: Configure Claude Code Hooks

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/slack-notify.sh"
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/slack-notify-waiting.sh"
          }
        ]
      }
    ]
  }
}
```

> **Note**: If you already have hooks configured, merge these entries into the existing arrays rather than overwriting.

### Step 6: Start

```bash
# Start a tmux session with Claude Code
tmux new -s claude
# Inside tmux, run: claude

# In a separate terminal, start the bot
cd ~/.claude/slack-bot && nohup python3 bot.py >> bot.log 2>&1 &
```

### Step 7: Verify

Send a message to your bot's DM in Slack:

- `status` -- See tmux screen content
- `sessions` -- List all active tmux sessions
- `hello` -- Sends "hello" to Claude Code

---

## Commands Reference

| Command | Description | Example |
|---------|-------------|---------|
| `<any text>` | Send instruction to Claude Code (auto-routed) | `run the tests` |
| `@session <text>` | Send to a specific tmux session | `@worker1 deploy to staging` |
| `status` | Show all sessions with last output line | `status` |
| `status <session>` | Show full screen capture of a session | `status worker1` |
| `sessions` | List all active tmux sessions | `sessions` |
| `ls` | Alias for `sessions` | `ls` |
| `y` / `n` | Approve or deny (auto-routed) | `y` |
| `cc: <text>` | Same as `<text>` (`cc:` prefix is optional) | `cc: fix the bug` |

### Interactive Elements

| Element | When It Appears | Action |
|---------|-----------------|--------|
| **Approve** button | Permission prompt notification | Sends `y` to the correct tmux session |
| **Deny** button | Permission prompt notification | Sends `n` to the correct tmux session |
| **Session picker** buttons | Multiple tmux sessions active | Routes your message to the selected session |

---

## Configuration

### Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `SLACK_NOTIFY_ENABLED` | Yes | Enable/disable notifications | `true` |
| `SLACK_BOT_TOKEN` | Yes | Bot User OAuth Token from Slack | `xoxb-...` |
| `SLACK_APP_TOKEN` | Yes | App-Level Token for Socket Mode | `xapp-...` |
| `SLACK_ALLOWED_USER` | Yes | Your Slack User ID (security filter) | `U0123456789` |
| `TMUX_SESSION_NAME` | No | Default tmux session name | `claude` (default) |

### Configuration File Location

The bot reads environment variables from `~/.config/ai-agents/profiles/default.env`. Alternatively, you can use a `.env` file in the project directory.

---

## Auto-Start with `tcc`

Add the following to your `~/.zshrc` to automatically start the Slack bot and Claude Code together:

```bash
# Slack Bot auto-start (kill existing -> restart)
_start_slack_bot() {
  pkill -f "slack-bot/bot.py" 2>/dev/null
  sleep 1
  cd ~/.claude/slack-bot && nohup python3 bot.py >> bot.log 2>&1 &
  cd - > /dev/null
}

# tmux + Claude Code + Slack Bot
tcc() {
  _start_slack_bot
  local name="${1:-$(basename "$PWD")}"
  name=$(echo "$name" | tr -c 'a-zA-Z0-9_-' '-' | sed 's/^-\+//;s/-\+$//;s/-\{2,\}/-/g')
  if [ -z "$name" ] || [ "${#name}" -le 1 ]; then
    echo "Could not derive session name from: $(basename "$PWD")"
    printf "Enter session name: "
    read name
    [ -z "$name" ] && echo "Cancelled" && return 1
    name=$(echo "$name" | tr -c 'a-zA-Z0-9_-' '-' | sed 's/^-\+//;s/-\+$//;s/-\{2,\}/-/g')
  fi
  if tmux has-session -t "$name" 2>/dev/null; then
    tmux attach -t "$name"
  else
    tmux new-session -d -s "$name" -c "$PWD"
    tmux send-keys -t "$name" "claude" Enter
    tmux attach -t "$name"
  fi
}

# Reattach to existing session (does NOT restart bot)
atcc() {
  local name="${1:-claude}"
  tmux attach -t "$name" 2>/dev/null || echo "Session '$name' not found. Start with tcc."
}
```

The bot process is fully independent from tmux sessions. Restarting it has zero impact on running Claude Code instances.

---

## Multi-Session Usage

```bash
# Terminal 1: main session
tcc main-project

# Terminal 2: worker session
tcc worker1

# From Slack:
# "run tests"             -> session picker buttons appear
# "@worker1 run tests"    -> sends directly to worker1
# "status"                -> shows both sessions with last output
```

---

## Deployment Modes

### Direct Deployment (Recommended)

Copy files manually as described in the [Quick Start](#quick-start) section. This gives you full control over file placement and configuration.

```
~/.claude/
  slack-bot/
    bot.py              # Slack bot (Socket Mode)
  hooks/
    slack-notify.sh     # Task completion hook
    slack-notify-waiting.sh  # Permission prompt hook
```

### Skill Deployment (Optional)

If you use Claude Code's Agent Skills feature, deploy as a skill for management via slash commands:

```bash
mkdir -p ~/.claude/skills/slack-bridge/hooks
cp skill/SKILL.md ~/.claude/skills/slack-bridge/
cp skill/bot.py ~/.claude/skills/slack-bridge/
cp skill/hooks/slack-notify.sh ~/.claude/skills/slack-bridge/hooks/
cp skill/hooks/slack-notify-waiting.sh ~/.claude/skills/slack-bridge/hooks/
```

Skill commands:

| Command | Description |
|---------|-------------|
| `/slack-bridge setup` | Guided first-time setup |
| `/slack-bridge start` | Start the bot |
| `/slack-bridge stop` | Stop the bot |
| `/slack-bridge status` | Check all components |

---

## How It Works

### Bot (`bot.py`)

The bot bridges two interfaces:

- **Slack API** via Socket Mode -- receives messages and button actions from your phone in real time
- **tmux CLI** via `send-keys` / `capture-pane` -- injects text into Claude Code and reads screen output

tmux provides the critical capability that regular terminals lack: **external I/O access**. `send-keys` injects input as if typed on the keyboard, while `capture-pane` reads the current screen contents.

### Hook Scripts

Hook scripts are triggered automatically by Claude Code's event system:

1. **Stop event** (`slack-notify.sh`):
   - Reads the hook payload from stdin (JSON with `cwd`, `last_assistant_message`)
   - Auto-detects the current tmux session name
   - Captures the tmux pane content (last 20 lines)
   - Sends a Block Kit notification to your Slack DM with Claude's response summary and screen capture

2. **Notification event** (`slack-notify-waiting.sh`):
   - Reads the hook payload from stdin (JSON with `cwd`, `message`)
   - Captures the tmux pane to show the actual permission prompt
   - Sends a Block Kit notification with **Approve** and **Deny** buttons
   - Button values include the tmux session name, ensuring actions route to the correct session

### Button Actions

When you tap **Approve** or **Deny** in Slack, the bot receives the action via Socket Mode and sends `y` or `n` to the correct tmux session. The session name is embedded in the button value, making multi-session approval reliable.

### PID-Based Process Management

The bot writes its PID to `~/.claude/slack-bot/bot.pid` on startup. If a previous instance is running, it is terminated before the new instance starts. This prevents duplicate bot processes.

---

## Project Structure

```
slack-bridge-for-claude-code/
  bot/
    bot.py                  # Main bot (Socket Mode + tmux integration)
    requirements.txt        # Python dependencies
  hooks/
    slack-notify.sh         # Stop hook (task completion notification)
    slack-notify-waiting.sh # Notification hook (permission prompt)
  skill/
    SKILL.md                # Claude Code Agent Skill definition
    bot.py                  # Bot copy for skill deployment
    hooks/
      slack-notify.sh
      slack-notify-waiting.sh
  tests/
    conftest.py             # Shared pytest fixtures
    test_bot.py             # Comprehensive test suite
  .github/
    workflows/
      ci.yml                # GitHub Actions CI (flake8, mypy, pytest)
  .env.example              # Environment variable template
  pyproject.toml            # Project metadata and tool configuration
  LICENSE                   # MIT License
```

---

## Testing

The project includes a comprehensive test suite covering tmux helpers, message parsing, authorization, and Slack event handler behavior.

```bash
# Install dev dependencies
pip install pytest flake8 mypy slack-bolt slack-sdk

# Run tests
pytest tests/ -v

# Run linting
flake8 bot/ --max-line-length=120

# Run type checking
mypy bot/ --ignore-missing-imports
```

### CI/CD

GitHub Actions runs on every push and pull request to `main`:

- **flake8** -- Code style and linting
- **mypy** -- Static type checking
- **pytest** -- Unit tests

---

## Requirements

- macOS or Linux with tmux
- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (MAX subscription recommended)
- A Slack workspace where you can create apps

---

## Contributing

Contributions are welcome. To get started:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes and ensure all tests pass (`pytest tests/ -v`)
4. Run linting and type checks (`flake8 bot/` and `mypy bot/`)
5. Commit your changes and open a pull request

Please follow the existing code style and include tests for new functionality.

---

## License

This project is licensed under the [MIT License](LICENSE).

Copyright (c) 2025 sohei-t
