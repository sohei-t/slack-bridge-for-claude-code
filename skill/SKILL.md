---
name: slack-bridge
description: Bidirectional bridge between Slack and Claude Code. Sends task completion and permission prompt notifications to Slack Bot DM, and accepts instructions from Slack. Works via tmux. Supports multi-session, Block Kit quick actions, and setup/start/stop/status commands.
argument-hint: "[setup|start|stop|status]"
---

# Slack Bridge - Bidirectional Slack + Claude Code Integration

Control Claude Code from your phone via Slack Bot DM. Supports multiple tmux sessions and Block Kit quick action buttons.

## Commands

Execute based on `$0`:

---

### `setup` - First-time setup

Run these steps in order:

#### 1. Install dependencies

```bash
pip3 install --quiet slack_bolt slack_sdk
which tmux || echo "tmux required: brew install tmux"
```

#### 2. Slack App creation guide

Walk the user through:

1. https://api.slack.com/apps -> Create New App -> From scratch
2. App name: `Claude Code Bridge`
3. Enable **Socket Mode** -> Generate App-Level Token (`connections:write` scope) -> note the `xapp-` token
4. **OAuth & Permissions** -> Add Bot Token Scopes:
   - `chat:write`, `im:history`, `im:write`, `users:read`
5. **Event Subscriptions** -> Enable -> Add Bot Event: `message.im`
6. Install to workspace -> note the `xoxb-` Bot User OAuth Token
7. Find your Slack User ID (Profile -> ... -> Copy member ID)

#### 3. Set environment variables

Add to `~/.config/ai-agents/profiles/default.env` or `.env`:

```
SLACK_NOTIFY_ENABLED=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_ALLOWED_USER=U...
TMUX_SESSION_NAME=claude
```

#### 4. Deploy bot file

```bash
mkdir -p ~/.claude/slack-bot
cp ~/.claude/skills/slack-bridge/bot.py ~/.claude/slack-bot/bot.py
```

#### 5. Deploy hook scripts

```bash
mkdir -p ~/.claude/hooks
cp ~/.claude/skills/slack-bridge/hooks/slack-notify.sh ~/.claude/hooks/slack-notify.sh
cp ~/.claude/skills/slack-bridge/hooks/slack-notify-waiting.sh ~/.claude/hooks/slack-notify-waiting.sh
chmod +x ~/.claude/hooks/slack-notify.sh
chmod +x ~/.claude/hooks/slack-notify-waiting.sh
```

#### 6. Configure Claude Code hooks

Add to `~/.claude/settings.json` hooks section (merge with existing, don't overwrite):

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

**Important**: If there are existing hooks, add to the arrays. Don't overwrite.

#### 7. Add tmux aliases to shell

Add to `~/.zshrc` or `~/.bashrc`:

```bash
# tmux + Claude Code
tcc() {
  local name="${1:-claude}"
  if tmux has-session -t "$name" 2>/dev/null; then
    tmux attach -t "$name"
  else
    tmux new -s "$name"
  fi
}
atcc() {
  local name="${1:-claude}"
  tmux attach -t "$name" 2>/dev/null || echo "Session '$name' not found. Start with tcc."
}
```

#### 8. Verify

```bash
cd ~/.claude/slack-bot && python3 bot.py &
sleep 3
curl -s -X POST "https://slack.com/api/auth.test" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Connected' if d.get('ok') else f'Error: {d.get(\"error\")}')"
```

After setup, let the user know:
- `tcc` starts tmux + Claude Code
- `tcc worker1` creates a named session for multi-session use
- Slack Bot DM accepts instructions
- Task completion and permission prompts trigger Slack notifications with action buttons

---

### `start` - Start the bot

```bash
if pgrep -f "slack-bot/bot.py" > /dev/null; then
  echo "Bot is already running (PID: $(pgrep -f 'slack-bot/bot.py' | head -1))"
else
  cd ~/.claude/slack-bot && nohup python3 bot.py >> bot.log 2>&1 &
  sleep 2
  pgrep -f "slack-bot/bot.py" > /dev/null && echo "Bot started" || echo "Failed to start. Check: tail ~/.claude/slack-bot/bot.log"
fi
```

---

### `stop` - Stop the bot

```bash
if pgrep -f "slack-bot/bot.py" > /dev/null; then
  pkill -f "slack-bot/bot.py"; sleep 1; echo "Bot stopped"
else
  echo "Bot is not running"
fi
```

---

### `status` - Check all components

```bash
echo "=== Slack Bridge Status ==="
pgrep -f "slack-bot/bot.py" > /dev/null && echo "Bot: running (PID: $(pgrep -f 'slack-bot/bot.py' | head -1))" || echo "Bot: stopped"
tmux list-sessions 2>/dev/null && echo "tmux: running" || echo "tmux: not found"
[ -f ~/.claude/hooks/slack-notify.sh ] && echo "Hook (completion): installed" || echo "Hook (completion): missing"
[ -f ~/.claude/hooks/slack-notify-waiting.sh ] && echo "Hook (waiting): installed" || echo "Hook (waiting): missing"
```

---

### No argument

Show usage:

```
Slack Bridge - Claude Code bidirectional notifications

Usage:
  /slack-bridge setup   - First-time setup
  /slack-bridge start   - Start bot
  /slack-bridge stop    - Stop bot
  /slack-bridge status  - Check status

Features:
  - Task completion notifications with tmux screen capture
  - Permission prompt notifications with approve/deny buttons
  - Send instructions from Slack DM to Claude Code
  - Multi-session support (@session_name, sessions/ls, menu)
  - Block Kit quick action buttons for one-tap approve/deny
```
