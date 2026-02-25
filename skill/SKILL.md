---
name: slack-bridge
description: Bidirectional bridge between Slack and Claude Code. Sends task completion and permission prompt notifications to Slack Bot DM, and accepts instructions from Slack. Works via tmux. Supports setup/start/stop/status commands.
argument-hint: "[setup|start|stop|status]"
---

# Slack Bridge - Bidirectional Slack + Claude Code Integration

Control Claude Code from your phone via Slack Bot DM.

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

1. https://api.slack.com/apps → Create New App → From scratch
2. App name: `Claude Code Bridge`
3. Enable **Socket Mode** → Generate App-Level Token (`connections:write` scope) → note the `xapp-` token
4. **OAuth & Permissions** → Add Bot Token Scopes:
   - `chat:write`, `im:history`, `im:write`, `users:read`
5. **Event Subscriptions** → Enable → Add Bot Event: `message.im`
6. Install to workspace → note the `xoxb-` Bot User OAuth Token
7. Find your Slack User ID (Profile → ... → Copy member ID)

#### 3. Set environment variables

Add to `~/.config/ai-agents/profiles/default.env` or `.env`:

```
SLACK_NOTIFY_ENABLED=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_ALLOWED_USER=U...
TMUX_SESSION_NAME=claude
```

#### 4. Deploy files

```bash
mkdir -p ~/.claude/slack-bot ~/.claude/hooks
cp ~/.claude/skills/slack-bridge/../bot/bot.py ~/.claude/slack-bot/bot.py
cp ~/.claude/skills/slack-bridge/../hooks/slack-notify.sh ~/.claude/hooks/
cp ~/.claude/skills/slack-bridge/../hooks/slack-notify-waiting.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/slack-notify*.sh
```

If the skill was installed standalone (not from the full repo), copy from the repo clone instead.

#### 5. Configure Claude Code hooks

Add to `~/.claude/settings.json` hooks section (merge with existing, don't overwrite):

```json
{
  "hooks": {
    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "bash ~/.claude/hooks/slack-notify.sh"}]}],
    "Notification": [{"matcher": "permission_prompt", "hooks": [{"type": "command", "command": "bash ~/.claude/hooks/slack-notify-waiting.sh"}]}]
  }
}
```

#### 6. Add tmux aliases to shell

Add to `~/.zshrc` or `~/.bashrc`:

```bash
tcc() {
  local name="${1:-claude}"
  if tmux has-session -t "$name" 2>/dev/null; then
    tmux attach -t "$name"
  else
    tmux new -s "$name"
  fi
}
```

#### 7. Verify

```bash
cd ~/.claude/slack-bot && python3 bot.py &
sleep 3
curl -s -X POST "https://slack.com/api/auth.test" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Connected' if d.get('ok') else f'Error: {d.get(\"error\")}')"
```

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
pgrep -f "slack-bot/bot.py" > /dev/null && echo "Bot: running" || echo "Bot: stopped"
tmux has-session -t "${TMUX_SESSION_NAME:-claude}" 2>/dev/null && echo "tmux: running" || echo "tmux: not found"
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
```
