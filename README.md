# Slack Bridge for Claude Code

Bidirectional bridge between Slack and [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Control Claude Code from your phone via Slack Bot DM.

## What it does

```
iPhone (Slack)  <->  Mac (bot.py)  <->  tmux  <->  Claude Code
```

| Direction | Feature | Details |
|-----------|---------|---------|
| Mac -> Phone | Task completion notification | Includes tmux screen capture + quick action buttons |
| Mac -> Phone | Permission prompt notification | Shows what Claude is asking + **Approve / Deny** buttons |
| Phone -> Mac | Send instructions | Type in Slack DM -> delivered to Claude Code |
| Phone -> Mac | Check status | `status` command shows current tmux screen |
| Phone -> Mac | Multi-session support | `@session_name` mention, `sessions`/`ls`, session picker buttons |
| Phone -> Mac | Quick action menu | `m`/`menu` shows one-tap buttons for common actions |

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
|  +-------------+     (with Block Kit buttons)  |
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

### Multi-session support

Run multiple Claude Code instances in separate tmux sessions and control them all from Slack:

- **Auto-detection**: If only one session exists, messages are sent automatically
- **Session picker**: If multiple sessions exist, Block Kit buttons let you choose the target
- **Direct mention**: `@worker1 run tests` sends directly to the `worker1` session
- **List sessions**: `sessions` or `ls` shows all active tmux sessions

### Block Kit quick action buttons

Interactive buttons appear directly in Slack notifications:

- **Permission prompts**: `Approve (y)` / `Deny (n)` buttons -- one tap to respond
- **Completion notifications**: `status` / `menu` buttons for quick follow-up
- **Quick menu** (`m` or `menu`): Shows `y`, `n`, `status`, `sessions` buttons; with multiple sessions, per-session buttons are also shown

### Commands

| Command | Description |
|---------|-------------|
| `<any text>` | Send instruction to Claude Code |
| `@session_name <text>` | Send to a specific tmux session |
| `status` | Show all sessions with last output line |
| `status <session>` | Show full screen capture of a specific session |
| `sessions` / `ls` | List all active tmux sessions |
| `m` / `menu` | Show quick action menu with buttons |
| `y` | Approve (send "y" to Claude Code) |
| `n` | Deny (send "n" to Claude Code) |
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
# Start tmux + Claude Code
tmux new -s claude
# Inside tmux, run: claude

# In another terminal, start the bot
cd ~/.claude/slack-bot && nohup python3 bot.py >> bot.log 2>&1 &
```

### 7. Test

Send a message to your bot's DM in Slack:
- `status` -> See tmux screen content
- `hello` -> Sends "hello" to Claude Code
- `y` -> Approve a permission prompt
- `m` -> Show quick action menu with buttons
- `sessions` -> List all active tmux sessions

## Multi-session usage

```bash
# Terminal 1: main session
tcc claude
# run claude inside

# Terminal 2: worker session
tcc worker1
# run claude inside

# From Slack:
# "run tests" -> buttons appear to choose claude or worker1
# "@worker1 run tests" -> sends directly to worker1
# "status" -> shows both sessions
# "m" -> shows per-session approve/deny/status buttons
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

## tmux Tips

```bash
# Convenient alias (add to ~/.zshrc)
tcc() {
  local name="${1:-claude}"
  if tmux has-session -t "$name" 2>/dev/null; then
    tmux attach -t "$name"
  else
    tmux new -s "$name"
  fi
}

# Usage
tcc           # Create or attach to "claude" session
tcc worker1   # Create or attach to "worker1" session
```

## How It Works

**bot.py** is a Python program that bridges two APIs:
- **Slack API** (Socket Mode) - receives messages from your phone
- **tmux CLI** (`send-keys` / `capture-pane`) - injects text into Claude Code

When multiple tmux sessions are running, the bot auto-detects them and presents Block Kit buttons for session selection. You can also use `@session_name` to target a specific session directly.

**Hook scripts** are triggered automatically by Claude Code:
- **Stop hook** -> fires when Claude finishes a task -> sends completion notification with `status` and `menu` buttons
- **Notification hook** -> fires when Claude needs permission -> sends the prompt details with `Approve` and `Deny` buttons

The buttons use Slack's Block Kit interactive components. When you tap a button, the bot receives the action and sends the corresponding keystroke (`y` or `n`) to the correct tmux session.

tmux provides the critical capability that regular terminals lack: **external I/O access** via `send-keys` (inject input) and `capture-pane` (read screen output).

## Requirements

- macOS (or Linux with tmux)
- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (MAX subscription recommended)
- A Slack workspace where you can create apps

## License

MIT
