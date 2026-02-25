#!/bin/bash
# Slack notification when Claude Code needs input (Notification hook)
# Sends a Bot DM with tmux pane capture showing what Claude is asking,
# plus instructions on how to respond.
#
# Hook type: Notification
# Matcher: permission_prompt
# Trigger: Claude Code requests permission (e.g., to run a bash command)
#
# Required env vars (via .env or environment):
#   SLACK_NOTIFY_ENABLED=true
#   SLACK_BOT_TOKEN=xoxb-...
#   SLACK_ALLOWED_USER=U...
#   TMUX_SESSION_NAME=claude (optional, default: claude)

# --- Load environment variables ---
for ENV_FILE in "./.env" "$HOME/.config/ai-agents/profiles/default.env"; do
  if [ -f "$ENV_FILE" ]; then
    [ -z "$SLACK_NOTIFY_ENABLED" ] && SLACK_NOTIFY_ENABLED=$(grep '^SLACK_NOTIFY_ENABLED=' "$ENV_FILE" | cut -d'=' -f2)
    [ -z "$SLACK_BOT_TOKEN" ] && SLACK_BOT_TOKEN=$(grep '^SLACK_BOT_TOKEN=' "$ENV_FILE" | cut -d'=' -f2)
    [ -z "$SLACK_ALLOWED_USER" ] && SLACK_ALLOWED_USER=$(grep '^SLACK_ALLOWED_USER=' "$ENV_FILE" | cut -d'=' -f2)
    [ -z "$TMUX_SESSION_NAME" ] && TMUX_SESSION_NAME=$(grep '^TMUX_SESSION_NAME=' "$ENV_FILE" | cut -d'=' -f2)
  fi
done

TMUX_SESSION_NAME="${TMUX_SESSION_NAME:-claude}"

# Exit if disabled or missing config
[ "$SLACK_NOTIFY_ENABLED" = "true" ] || exit 0
[ -n "$SLACK_BOT_TOKEN" ] || exit 0
[ -n "$SLACK_ALLOWED_USER" ] || exit 0

# Read JSON from stdin
INPUT=$(cat)

# Build and send notification via Python
export HOOK_INPUT="$INPUT"
export TMUX_SESSION_NAME
export BOT_TOKEN="$SLACK_BOT_TOKEN"
export ALLOWED_USER="$SLACK_ALLOWED_USER"

(
python3 << 'PYEOF'
import json, subprocess, sys, os, urllib.request
from datetime import datetime

try:
    data = json.loads(os.environ.get("HOOK_INPUT", "{}"))
except:
    data = {}

cwd = data.get("cwd", "")
message = data.get("message", "")
dir_name = os.path.basename(cwd) if cwd else ""
time_str = datetime.now().strftime("%H:%M:%S")
tmux_session = os.environ.get("TMUX_SESSION_NAME", "claude")
bot_token = os.environ.get("BOT_TOKEN", "")
allowed_user = os.environ.get("ALLOWED_USER", "")

if not bot_token or not allowed_user:
    sys.exit(0)

# Capture tmux pane to show what Claude is actually asking
pane_content = ""
try:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", tmux_session, "-p", "-l", "40"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0:
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        pane_content = "\n".join(lines[-20:])
except:
    pass

# Build message
parts = [
    f":double_vertical_bar: *Waiting for input: {dir_name}*",
    f":speech_balloon: {message}",
    f":clock3: {time_str}",
]
if pane_content:
    parts.append("")
    parts.append(f"```\n{pane_content}\n```")
parts.append("")
parts.append("*Reply to this DM to respond:*")
parts.append("`y` = allow  `n` = deny  anything else = send as instruction")

text = "\n".join(parts)

# Send via Bot DM
headers = {
    "Authorization": f"Bearer {bot_token}",
    "Content-Type": "application/json; charset=utf-8",
}

req = urllib.request.Request(
    "https://slack.com/api/conversations.open",
    data=json.dumps({"users": allowed_user}).encode("utf-8"),
    headers=headers, method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        channel_id = json.loads(resp.read()).get("channel", {}).get("id", "")
except:
    sys.exit(0)

if not channel_id:
    sys.exit(0)

req2 = urllib.request.Request(
    "https://slack.com/api/chat.postMessage",
    data=json.dumps({"channel": channel_id, "text": text}).encode("utf-8"),
    headers=headers, method="POST",
)
try:
    urllib.request.urlopen(req2, timeout=10)
except:
    pass
PYEOF
) &

exit 0
