#!/bin/bash
set -euo pipefail
# Slack 入力待ち通知スクリプト（Bot DM版）
# Claude Code の Notification フックから呼び出され、承認待ち時に Bot DM に通知を送信する
# tmux 画面キャプチャを含めて、何を問われているかを明確に伝える

# デバッグログ
echo "$(date '+%Y-%m-%d %H:%M:%S') [slack-notify-waiting] Hook fired" >> "$HOME/.claude/slack-bot/hook.log"

ENV_FILE="$HOME/.config/ai-agents/profiles/default.env"

# 環境変数読み込み
if [ -f "$ENV_FILE" ]; then
  SLACK_NOTIFY_ENABLED=$(grep '^SLACK_NOTIFY_ENABLED=' "$ENV_FILE" | cut -d'=' -f2)
  SLACK_BOT_TOKEN=$(grep '^SLACK_BOT_TOKEN=' "$ENV_FILE" | cut -d'=' -f2)
  SLACK_ALLOWED_USER=$(grep '^SLACK_ALLOWED_USER=' "$ENV_FILE" | cut -d'=' -f2)
  TMUX_SESSION_NAME=$(grep '^TMUX_SESSION_NAME=' "$ENV_FILE" | cut -d'=' -f2)
fi

TMUX_SESSION_NAME="${TMUX_SESSION_NAME:-claude}"

# 無効なら即終了
[ "$SLACK_NOTIFY_ENABLED" = "true" ] || exit 0
[ -n "$SLACK_BOT_TOKEN" ] || exit 0
[ -n "$SLACK_ALLOWED_USER" ] || exit 0

# stdin から JSON を読み取り
INPUT=$(cat)

# 環境変数を設定してからバックグラウンド実行
export HOOK_INPUT="$INPUT"
export TMUX_SESSION_NAME
export BOT_TOKEN="$SLACK_BOT_TOKEN"
export ALLOWED_USER="$SLACK_ALLOWED_USER"

(
python3 << 'PYEOF'
import json, subprocess, sys, os, urllib.request
from datetime import datetime
from pathlib import Path

PENDING_FILE = Path.home() / ".claude/slack-bot/pending_approvals.json"

# 入力解析
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

# 実行中の tmux セッション名を検出（環境変数 TMUX から自動判定）
try:
    detect = subprocess.run(
        ["tmux", "display-message", "-p", "#{session_name}"],
        capture_output=True, text=True, timeout=3
    )
    if detect.returncode == 0 and detect.stdout.strip():
        tmux_session = detect.stdout.strip()
except:
    pass

# --- 前回の未解決通知を「許可済み」に更新 ---
def resolve_pending(token, resolve_text=":white_check_mark: *ローカルで許可済み*"):
    if not PENDING_FILE.exists():
        return
    try:
        pending = json.loads(PENDING_FILE.read_text())
    except:
        pending = []
    for entry in pending:
        try:
            update_payload = {
                "channel": entry["channel"],
                "ts": entry["ts"],
                "text": resolve_text,
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": resolve_text}}],
            }
            req = urllib.request.Request(
                "https://slack.com/api/chat.update",
                data=json.dumps(update_payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except:
            pass
    PENDING_FILE.unlink(missing_ok=True)

resolve_pending(bot_token)

# tmux 画面キャプチャ（末尾部分）- 実際の許可プロンプト内容を取得
pane_content = ""
try:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", tmux_session, "-p", "-S", "-40"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0:
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        pane_content = "\n".join(lines[-20:])
except:
    pass

# メッセージ組み立て（Block Kit）
header_text = f":double_vertical_bar: *入力待ち: {dir_name}*\n:speech_balloon: {message}\n:clock3: {time_str}"

blocks = [
    {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
]

if pane_content:
    # Slack の code block は最大 3000 文字程度
    if len(pane_content) > 2500:
        pane_content = "...\n" + pane_content[-2500:]
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```\n{pane_content}\n```"}})

# 許可/拒否ボタン（action_id は bot.py の hook_approve / hook_deny ハンドラに対応）
blocks.append({
    "type": "actions",
    "elements": [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "✅ 許可 (y)"},
            "action_id": "hook_approve",
            "value": tmux_session,
            "style": "primary",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "❌ 拒否 (n)"},
            "action_id": "hook_deny",
            "value": tmux_session,
            "style": "danger",
        },
    ],
})

fallback_text = f"入力待ち: {dir_name} - {message}"

# Bot DM で送信
headers = {
    "Authorization": f"Bearer {bot_token}",
    "Content-Type": "application/json; charset=utf-8",
}

# conversations.open
req = urllib.request.Request(
    "https://slack.com/api/conversations.open",
    data=json.dumps({"users": allowed_user}).encode("utf-8"),
    headers=headers,
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        channel_id = json.loads(resp.read()).get("channel", {}).get("id", "")
except:
    sys.exit(0)

if not channel_id:
    sys.exit(0)

# chat.postMessage（Block Kit）
payload = {
    "channel": channel_id,
    "text": fallback_text,
    "blocks": blocks,
}
req2 = urllib.request.Request(
    "https://slack.com/api/chat.postMessage",
    data=json.dumps(payload).encode("utf-8"),
    headers=headers,
    method="POST",
)
msg_ts = ""
try:
    with urllib.request.urlopen(req2, timeout=10) as resp:
        msg_ts = json.loads(resp.read()).get("ts", "")
except:
    pass

# 送信したメッセージ情報を保存（次回フック発火時に更新するため）
if channel_id and msg_ts:
    pending = []
    if PENDING_FILE.exists():
        try:
            pending = json.loads(PENDING_FILE.read_text())
        except:
            pass
    # tmux 末尾5行をスナップショットとして保存（Bot のポーリングで画面変化を検出するため）
    snapshot = ""
    try:
        snap_result = subprocess.run(
            ["tmux", "capture-pane", "-t", tmux_session, "-p", "-S", "-5"],
            capture_output=True, text=True, timeout=3
        )
        if snap_result.returncode == 0:
            snapshot = snap_result.stdout.strip()
    except:
        pass
    pending.append({"channel": channel_id, "ts": msg_ts, "session": tmux_session, "pane_snapshot": snapshot})
    PENDING_FILE.write_text(json.dumps(pending))

PYEOF
) &

exit 0
