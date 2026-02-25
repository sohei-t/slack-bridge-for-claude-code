![CI](https://github.com/sohei-t/slack-bridge-for-claude-code/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.10+-blue)
![License](https://img.shields.io/badge/License-MIT-green)

# Slack Bridge for Claude Code

Bidirectional bridge between Slack and [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Control Claude Code from your phone via Slack Bot DM.

## What it does

```
iPhone (Slack)  <->  Mac (bot.py)  <->  tmux  <->  Claude Code
```

| Direction | Feature | Details |
|-----------|---------|---------|
| Mac -> Phone | Task completion notification | Shows Claude's response summary + tmux screen capture |
| Mac -> Phone | Permission prompt notification | Shows what Claude is asking + **Approve / Deny** buttons |
| Phone -> Mac | Send instructions | Type in Slack DM -> delivered to Claude Code |
| Phone -> Mac | Check status | `status` command shows current tmux screen |
| Phone -> Mac | Multi-session support | `@session_name` mention or auto session picker |

## Architecture

```
+----------------------------------------------+
|  Your Mac (always-on)                         |
|                                               |
|  +-------------+   +---------------------+   |
|  | bot.py      |   | tmux "claude"       |   |
|  | (Slack API  |-->|  +---------------+   |   |
|  |  client)    |   |  | Claude Code   |   |   |
|  +-------------+   |  +-------+-------+   |   |
|                     +---------++-----------+   |
|  +-------------+              ||               |
|  | Hook scripts|<-------------+|               |
|  | (auto-fired |  Stop / Notification event    |
|  |  by Claude) |--> Slack Bot DM               |
|  +-------------+                               |
+------------------------------------------------+
         | Internet (Slack API, Socket Mode)
+------------------+
|  Phone (Slack)   |
|  - Get notified  |
|  - Tap buttons   |
|  - Send commands |
+------------------+
```

## Features

### Notifications

- **Task completion**: When Claude finishes, you get a notification with Claude's response summary and a tmux screen capture (no buttons -- just informational)
- **Permission prompt**: When Claude needs approval, you get the prompt details with **Approve** and **Deny** buttons for one-tap response

### Multi-session support

Run multiple Claude Code instances in separate tmux sessions and control them all from Slack:

- **Auto-detection**: If only one session exists, messages are sent automatically
- **Session picker**: If multiple sessions exist, Block Kit buttons let you choose the target
- **Direct mention**: `@worker1 run tests` sends directly to the `worker1` session

### Commands

| Command | Description |
|---------|-------------|
| `<any text>` | Send instruction to Claude Code (auto-routed) |
| `@session_name <text>` | Send to a specific tmux session |
| `status` | Show all sessions with last output line |
| `status <session>` | Show full screen capture of a specific session |
| `sessions` / `ls` | List all active tmux sessions |
| `y` / `n` | Approve or deny (auto-routed like any text) |
| `cc: <text>` | Same as `<text>` (cc: prefix is optional) |

## Quick Start

### 1. Install dependencies

```bash
pip3 install slack_bolt slack_sdk
brew install tmux  # if not installed
```

### 2. Create a Slack App

1. Go to https://api.slack.com/apps -> **Create New App** -> **From scratch**
2. Name it (e.g., `Claude Code Bridge`)
3. Enable **Socket Mode**:
   - Basic Information -> App-Level Tokens -> Generate Token
   - Scope: `connections:write`
   - Save the `xapp-...` token
4. Add **Bot Token Scopes** (OAuth & Permissions):
   - `chat:write` - Send messages
   - `im:history` - Read DM history
   - `im:write` - Open DM channels
   - `users:read` - Read user info
5. Enable **Event Subscriptions**:
   - Subscribe to bot event: `message.im`
6. **Install to Workspace**
   - Save the `xoxb-...` Bot User OAuth Token
7. Find your **Slack User ID**:
   - Click your profile -> **...** -> **Copy member ID**

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your tokens and user ID
```

Or add to `~/.config/ai-agents/profiles/default.env`:

```
SLACK_NOTIFY_ENABLED=true
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_APP_TOKEN=xapp-your-token
SLACK_ALLOWED_USER=U0000000000
TMUX_SESSION_NAME=claude
```

### 4. Deploy

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

### 5. Configure Claude Code hooks

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "bash ~/.claude/hooks/slack-notify.sh"}
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt",
        "hooks": [
          {"type": "command", "command": "bash ~/.claude/hooks/slack-notify-waiting.sh"}
        ]
      }
    ]
  }
}
```

### 6. Start

```bash
# Recommended: add tcc function to ~/.zshrc (auto-starts bot)
tcc           # Creates tmux session + starts Claude Code + starts bot

# Or manually:
tmux new -s claude
# Inside tmux: claude

# In another terminal:
cd ~/.claude/slack-bot && nohup python3 bot.py >> bot.log 2>&1 &
```

### 7. Test

Send a message to your bot's DM in Slack:
- `status` -> See tmux screen content
- `hello` -> Sends "hello" to Claude Code
- `sessions` -> List all active tmux sessions

## Auto bot startup with `tcc`

Add this to your `~/.zshrc` to automatically start the Slack bot whenever you use `tcc`:

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

The bot is killed and restarted each time `tcc` runs. This is safe -- bot.py is completely independent from tmux sessions. Restarting it has zero impact on running Claude Code instances.

## Multi-session usage

```bash
# Terminal 1: main session
tcc claude

# Terminal 2: worker session
tcc worker1

# From Slack:
# "run tests" -> session picker buttons appear (choose claude or worker1)
# "@worker1 run tests" -> sends directly to worker1
# "status" -> shows both sessions
```

## Agent Skill (optional)

If you use Claude Code's Agent Skills feature, copy the skill for quick management:

```bash
mkdir -p ~/.claude/skills/slack-bridge/hooks
cp skill/SKILL.md ~/.claude/skills/slack-bridge/
cp skill/bot.py ~/.claude/skills/slack-bridge/
cp skill/hooks/slack-notify.sh ~/.claude/skills/slack-bridge/hooks/
cp skill/hooks/slack-notify-waiting.sh ~/.claude/skills/slack-bridge/hooks/
```

Then use:
- `/slack-bridge setup` - Guided first-time setup
- `/slack-bridge start` - Start the bot
- `/slack-bridge stop` - Stop the bot
- `/slack-bridge status` - Check all components

## How It Works

**bot.py** bridges two APIs:
- **Slack API** (Socket Mode) - receives messages from your phone
- **tmux CLI** (`send-keys` / `capture-pane`) - injects text into Claude Code

When multiple tmux sessions are running, the bot auto-detects them and presents session picker buttons. You can also use `@session_name` to target a specific session directly.

**Hook scripts** are triggered automatically by Claude Code events:
- **Stop hook** (`slack-notify.sh`) -> fires when Claude finishes a task -> sends completion notification with Claude's response summary and tmux screen capture
- **Notification hook** (`slack-notify-waiting.sh`) -> fires when Claude needs permission -> sends prompt details with **Approve** and **Deny** buttons

The Approve/Deny buttons use Slack's Block Kit interactive components. When you tap a button, the bot receives the action via Socket Mode and sends `y` or `n` to the correct tmux session. These are the **only** buttons in the system -- everything else is handled by typing text.

tmux provides the critical capability that regular terminals lack: **external I/O access** via `send-keys` (inject input) and `capture-pane` (read screen output).

## Requirements

- macOS (or Linux with tmux)
- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (MAX subscription recommended)
- A Slack workspace where you can create apps

## License

MIT
