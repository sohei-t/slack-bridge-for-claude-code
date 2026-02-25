#!/usr/bin/env python3
"""
Slack → Claude Code ブリッジ Bot（マルチセッション対応）
Slack DM で受け取ったメッセージを tmux 内の Claude Code に送信する

使い方:
  Slack DM で:
    テスト実行して              → セッション1つなら自動送信、複数ならボタン選択
    @worker1 テスト実行して     → 直接 worker1 セッションに送信
    status                     → 全セッションの状態を確認
    status claude              → 特定セッションの画面を確認
    sessions / ls              → セッション一覧

環境変数 (~/.config/ai-agents/profiles/default.env):
  SLACK_BOT_TOKEN=xoxb-...     # Bot User OAuth Token
  SLACK_APP_TOKEN=xapp-...     # App-Level Token (Socket Mode用)
  SLACK_ALLOWED_USER=U...      # 許可するSlackユーザーID（自分のみ）
  TMUX_SESSION_NAME=claude     # デフォルトセッション名 (default: claude)
"""

import json
import os
import re
import subprocess
import logging
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# --- 設定読み込み ---

ENV_FILE = Path.home() / ".config/ai-agents/profiles/default.env"


def load_env():
    """default.env から環境変数を読み込む"""
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"{ENV_FILE} が見つかりません")
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


env = load_env()

SLACK_BOT_TOKEN = env.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = env.get("SLACK_APP_TOKEN", "")
SLACK_ALLOWED_USER = env.get("SLACK_ALLOWED_USER", "")
DEFAULT_SESSION = env.get("TMUX_SESSION_NAME", "claude")

if not SLACK_BOT_TOKEN:
    raise ValueError("SLACK_BOT_TOKEN が未設定です")
if not SLACK_APP_TOKEN:
    raise ValueError("SLACK_APP_TOKEN が未設定です")
if not SLACK_ALLOWED_USER:
    raise ValueError("SLACK_ALLOWED_USER が未設定です（セキュリティのため必須）")

# --- ログ設定 ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / ".claude/slack-bot/bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# --- 保留メッセージ（ボタン選択待ち用） ---

pending_messages = {}

# --- tmux 操作 ---


def tmux_list_sessions() -> list[str]:
    """稼働中の tmux セッション一覧を取得"""
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return [s.strip() for s in result.stdout.splitlines() if s.strip()]


def tmux_session_exists(session: str) -> bool:
    """指定した tmux セッションが存在するか確認"""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    return result.returncode == 0


def tmux_send(session: str, text: str) -> bool:
    """tmux セッションにテキストを送信"""
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
    """tmux セッションの現在の表示内容を取得（直近50行）"""
    if not tmux_session_exists(session):
        return "(セッションなし)"
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-l", "50"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or "(空)"


# --- メッセージ解析 ---


def parse_mention(text: str) -> tuple[str | None, str]:
    """@セッション名 を解析。 '@worker1 テスト' → ('worker1', 'テスト')"""
    m = re.match(r"^@(\S+)\s+(.*)", text, re.DOTALL)
    if m:
        return m.group(1), m.group(2).strip()
    return None, text


# --- Slack Bot ---

app = App(token=SLACK_BOT_TOKEN)


def is_allowed(user_id: str) -> bool:
    """許可されたユーザーかチェック"""
    return user_id == SLACK_ALLOWED_USER


@app.event("message")
def handle_message(event, say):
    """DM メッセージを処理"""
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

    # cc: プレフィックス除去（オプション）
    prompt = text
    if text.lower().startswith("cc:"):
        prompt = text[3:].strip()

    if not prompt:
        say("メッセージが空です。指示を入力してください。")
        return

    # --- 特殊コマンド ---
    cmd = prompt.lower().strip()

    # status
    if cmd.startswith("status"):
        handle_status(prompt, say)
        return

    # sessions / ls
    if cmd in ("sessions", "ls"):
        sessions = tmux_list_sessions()
        if not sessions:
            say(":x: tmux セッションが見つかりません。`tcc` で起動してください。")
            return
        lines = [f":computer: *セッション一覧 ({len(sessions)}個)*"]
        for s in sessions:
            lines.append(f"  • `{s}`")
        say("\n".join(lines))
        return

    # --- @メンション解析 ---
    target_session, prompt = parse_mention(prompt)

    if target_session:
        send_to_session(target_session, prompt, say)
        return

    # --- セッション自動判定 ---
    sessions = tmux_list_sessions()

    if len(sessions) == 0:
        say(":x: tmux セッションが見つかりません。Mac で `tcc` を実行してください。")
        return

    if len(sessions) == 1:
        send_to_session(sessions[0], prompt, say)
        return

    # セッション複数 → ボタン選択
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
                "text": f":arrow_right: *送信先を選択してください:*\n> {prompt}",
            },
        },
        {"type": "actions", "elements": buttons},
    ]

    say(blocks=blocks, text="送信先を選択してください")


def handle_status(prompt: str, say):
    """status コマンド処理"""
    parts = prompt.split(maxsplit=1)
    sessions = tmux_list_sessions()

    if len(parts) >= 2:
        target = parts[1].strip()
        if tmux_session_exists(target):
            pane = tmux_capture(target)
            if len(pane) > 2500:
                pane = "...\n" + pane[-2500:]
            say(f":white_check_mark: `{target}` は稼働中\n```\n{pane}\n```")
        else:
            say(f":x: `{target}` が見つかりません。")
        return

    if not sessions:
        say(":x: tmux セッションが見つかりません。`tcc` で起動してください。")
        return

    lines = []
    for s in sessions:
        pane = tmux_capture(s)
        last_lines = [l for l in pane.splitlines() if l.strip()]
        last_line = last_lines[-1] if last_lines else "(空)"
        if len(last_line) > 80:
            last_line = last_line[:80] + "..."
        lines.append(f":white_check_mark: `{s}`: {last_line}")

    say(f":computer: セッション一覧 ({len(sessions)}個):\n" + "\n".join(lines))


def send_to_session(session: str, prompt: str, say):
    """指定セッションにメッセージを送信"""
    if not tmux_session_exists(session):
        say(f":x: `{session}` が見つかりません。")
        return

    if tmux_send(session, prompt):
        log.info(f"Sent to tmux:{session}: {prompt[:80]}")
        say(f":arrow_right: `{session}` に送信しました:\n> {prompt}")
    else:
        say(f":x: `{session}` への送信に失敗しました。")


# --- ボタンハンドラ ---


# セッション選択ボタン（複数セッション時の送信先選択）
@app.action(re.compile(r"send_to_.*"))
def handle_session_select(ack, action, respond, say, body):
    """セッション選択ボタンが押された時の処理"""
    ack()
    user = body.get("user", {}).get("id", "")
    if not is_allowed(user):
        return

    try:
        data = json.loads(action["value"])
    except (json.JSONDecodeError, KeyError):
        respond(text=":x: エラーが発生しました。もう一度送信してください。", replace_original=True)
        return

    msg_id = data.get("msg_id", "")
    session = data.get("session", "")
    prompt = pending_messages.pop(msg_id, None)

    if prompt is None:
        respond(text=":warning: メッセージの有効期限が切れました。", replace_original=True)
        return

    if tmux_send(session, prompt):
        log.info(f"Sent to tmux:{session}: {prompt[:80]}")
        respond(text=f":arrow_right: `{session}` に送信: {prompt[:60]}", replace_original=True)
    else:
        respond(text=f":x: `{session}` への送信に失敗", replace_original=True)


# 入力待ち通知からの許可/拒否ボタン（Hook スクリプトが送信）
@app.action("hook_approve")
def handle_hook_approve(ack, action, respond, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    session = action.get("value", "") or DEFAULT_SESSION
    if tmux_send(session, "y"):
        respond(text=f":white_check_mark: `{session}` を許可しました", replace_original=True)
    else:
        respond(text=f":x: `{session}` への送信に失敗しました", replace_original=True)


@app.action("hook_deny")
def handle_hook_deny(ack, action, respond, body):
    ack()
    if not is_allowed(body.get("user", {}).get("id", "")):
        return
    session = action.get("value", "") or DEFAULT_SESSION
    if tmux_send(session, "n"):
        respond(text=f":no_entry_sign: `{session}` を拒否しました", replace_original=True)
    else:
        respond(text=f":x: `{session}` への送信に失敗しました", replace_original=True)


# --- 起動 ---


def main():
    log.info(f"Slack Bot 起動中... (default session: {DEFAULT_SESSION})")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
