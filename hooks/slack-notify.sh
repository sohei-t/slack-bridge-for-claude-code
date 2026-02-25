#!/bin/bash
set -euo pipefail
# Slack タスク完了通知スクリプト（Bot DM版）
# Claude Code の Stop フックから呼び出され、タスク完了時に Bot DM に通知を送信する
# tmux の画面内容も含めて送信する

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

# 入力解析
try:
    data = json.loads(os.environ.get("HOOK_INPUT", "{}"))
except:
    data = {}

cwd = data.get("cwd", "")
last_message = data.get("last_assistant_message", "")
dir_name = os.path.basename(cwd) if cwd else ""
time_str = datetime.now().strftime("%H:%M:%S")
tmux_session = os.environ.get("TMUX_SESSION_NAME", "claude")
bot_token = os.environ.get("BOT_TOKEN", "")
allowed_user = os.environ.get("ALLOWED_USER", "")

if not bot_token or not allowed_user:
    sys.exit(0)

# 実行中の tmux セッション名を検出
try:
    detect = subprocess.run(
        ["tmux", "display-message", "-p", "#{session_name}"],
        capture_output=True, text=True, timeout=3
    )
    if detect.returncode == 0 and detect.stdout.strip():
        tmux_session = detect.stdout.strip()
except:
    pass

# tmux 画面キャプチャ（末尾20行）
pane_content = ""
try:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", tmux_session, "-p", "-l", "30"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0:
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        pane_content = "\n".join(lines[-20:])
except:
    pass

# last_assistant_message を要約（長すぎる場合は先頭部分を使用）
summary = ""
if last_message:
    # 最初の意味のある数行を抽出（箇条書き・見出し等を優先）
    msg_lines = [l for l in last_message.strip().splitlines() if l.strip()]
    # 先頭500文字以内に収める
    buf = []
    total = 0
    for line in msg_lines:
        if total + len(line) > 500:
            buf.append("...")
            break
        buf.append(line)
        total += len(line)
    summary = "\n".join(buf) if buf else last_message[:500]

# メッセージ組み立て（Block Kit）
header_text = ":white_check_mark: *Claude Code 完了*"
if dir_name:
    header_text += f"\n:file_folder: {dir_name}"
header_text += f"\n:clock3: {time_str}"

blocks = [
    {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
]

# Claude の応答要約を表示
if summary:
    # Slack の section text は 3000 文字制限
    if len(summary) > 2500:
        summary = summary[:2500] + "..."
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary}})

# tmux 画面キャプチャ（要約がない場合のフォールバック、または補足情報）
if pane_content:
    if len(pane_content) > 2000:
        pane_content = "...\n" + pane_content[-2000:]
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```\n{pane_content}\n```"}})

# 完了通知にはボタン不要
# - 許可/拒否 → 入力待ち通知のボタンで対応
# - 次の指示 → テキスト入力 → セッション自動選択
# - 状態確認 → "status" と入力

fallback_text = f"Claude Code 完了 - {dir_name} ({time_str})"

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
try:
    urllib.request.urlopen(req2, timeout=10)
except:
    pass

PYEOF
) &

exit 0
