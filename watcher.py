"""Release Watcher: GitHub Releases → Claude Haiku summary → Telegram."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from anthropic import Anthropic

REPOS = [
    "n8n-io/n8n",
    "anthropics/claude-code",
    "openai/codex",
    "openclaw/openclaw",
]

EMOJI = {
    "n8n-io/n8n": "🟣",
    "anthropics/claude-code": "🤖",
    "openai/codex": "🧠",
    "openclaw/openclaw": "🦞",
}

DISPLAY_NAME = {
    "n8n-io/n8n": "n8n",
    "anthropics/claude-code": "Claude Code",
    "openai/codex": "Codex",
    "openclaw/openclaw": "OpenClaw",
}

STATE_PATH = Path(__file__).parent / "state.json"
CLAUDE_MODEL = "claude-haiku-4-5"

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

anthropic_client = Anthropic(api_key=ANTHROPIC_KEY)


def log(msg: str) -> None:
    print(f"[{datetime.utcnow().isoformat()}Z] {msg}", flush=True)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    text = STATE_PATH.read_text().strip()
    return json.loads(text) if text else {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def state_entry(release: dict) -> dict:
    return {
        "last_release_id": release["id"],
        "last_tag": release["tag_name"],
        "last_published_at": release["published_at"],
    }


def fetch_releases(repo: str) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/releases"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "release-watcher"}
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=20) as client:
                r = client.get(url, headers=headers, params={"per_page": 10})
            if r.status_code == 404:
                log(f"  {repo}: 404 (приватный или не существует), пропускаем")
                return []
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, httpx.RequestError) as e:
            last_err = e
            wait = 2**attempt
            log(f"  {repo}: GitHub API fail ({e}), retry через {wait}s")
            time.sleep(wait)
    log(f"  {repo}: GitHub API недоступен, пропускаем (last error: {last_err})")
    return []


def summarize_with_claude(release: dict, repo: str) -> str:
    title = release.get("name") or release["tag_name"]
    body = (release.get("body") or "").strip()
    body_excerpt = body[:4000] if body else ""

    if not body_excerpt:
        prompt = (
            f"Релиз {DISPLAY_NAME[repo]} версии {release['tag_name']} "
            f"с заголовком '{title}'. Release notes пустой.\n\n"
            f"Напиши одно короткое предположение (1 предложение, до 150 символов), "
            f"что может быть в этом релизе по версии и заголовку. На русском, без воды."
        )
    else:
        prompt = (
            f"Это release notes проекта {DISPLAY_NAME[repo]} версии {release['tag_name']}.\n\n"
            f"```\n{body_excerpt}\n```\n\n"
            f"Напиши краткое саммари на русском (2-3 предложения, до 400 символов). "
            f"Только суть: что нового, что починили, что важно для пользователя. "
            f"Без вступлений, без 'в этом релизе', без markdown. Простой текст."
        )

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            response = anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if len(text) >= 30:
                return text
            log(f"  Claude вернул слишком короткий ответ ({len(text)} симв), fallback")
            break
        except Exception as e:
            last_err = e
            log(f"  Claude API fail ({e}), retry через {2**attempt}s")
            time.sleep(2**attempt)

    if last_err:
        log(f"  Claude недоступен (last error: {last_err}), fallback на raw notes")
    fallback = body_excerpt[:300] if body_excerpt else title
    return fallback


MD_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def md_escape(text: str) -> str:
    return "".join("\\" + c if c in MD_V2_SPECIAL else c for c in text)


RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"{dt.day} {RU_MONTHS[dt.month - 1]} {dt.year}"


def build_message(release: dict, repo: str, summary: str) -> str:
    emoji = EMOJI[repo]
    name = DISPLAY_NAME[repo]
    tag = release["tag_name"]
    url = release["html_url"]
    date = format_date(release["published_at"])

    return (
        f"{emoji} *{md_escape(name)}* — `{md_escape(tag)}`\n"
        f"\n"
        f"{md_escape(summary)}\n"
        f"\n"
        f"🔗 {md_escape(url)}\n"
        f"📅 {md_escape(date)}"
    )


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False,
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=15) as client:
                r = client.post(url, json=payload)
            if r.status_code == 401:
                log("  Telegram 401 — токен битый, выход")
                sys.exit(1)
            if r.status_code == 200:
                return True
            log(f"  Telegram {r.status_code}: {r.text[:200]}")
            last_err = httpx.HTTPStatusError("bad status", request=r.request, response=r)
        except (httpx.HTTPError, httpx.RequestError) as e:
            last_err = e
            log(f"  Telegram fail ({e})")
        time.sleep(2**attempt)
    log(f"  Telegram недоступен (last error: {last_err}), state НЕ обновляем")
    return False


def main() -> None:
    state = load_state()
    bootstrap = not state
    if bootstrap:
        log("Bootstrap режим: state пустой, сообщения слаться НЕ будут")

    sent_count = 0
    for repo in REPOS:
        log(f"Repo: {repo}")
        releases = fetch_releases(repo)
        if not releases:
            continue

        last_id = state.get(repo, {}).get("last_release_id")
        new = [
            r for r in releases
            if (last_id is None or r["id"] > last_id)
            and not r.get("draft", False)
            and not r.get("prerelease", False)
            and re.search(r"\d", r["tag_name"])  # отсекаем плавающие теги (stable/beta/latest)
        ]
        new.sort(key=lambda r: r["published_at"])

        if not new:
            log(f"  Нет новых релизов (last_id={last_id})")
            continue

        if bootstrap:
            latest = max(new, key=lambda r: r["id"])
            state[repo] = state_entry(latest)
            log(f"  Bootstrap: записали в state {latest['tag_name']}, не шлём")
            continue

        log(f"  Новых релизов: {len(new)}")
        for release in new:
            log(f"  Обрабатываю {release['tag_name']} (id={release['id']})")
            summary = summarize_with_claude(release, repo)
            text = build_message(release, repo, summary)
            if send_telegram(text):
                state[repo] = state_entry(release)
                sent_count += 1
                log(f"  ✓ отправлено")
            else:
                log(f"  ✗ не отправлено, прерываем repo (попробуем завтра)")
                break

    save_state(state)
    log(f"Готово. Отправлено сообщений: {sent_count}")


if __name__ == "__main__":
    main()
