# release-watcher

Раз в день мониторит GitHub Releases в `n8n-io/n8n`, `anthropics/claude-code`,
`openai/codex`, `openclaw/openclaw`. Каждый новый релиз прогоняется через
Claude Haiku 4.5 и публикуется отдельным сообщением в Telegram — от имени
**@vb_regular_alert_bot** (рассыльный бот; @vibe_business_official_bot для рассылок
не используется, на нём держатся платежи).

## Куда что уходит

| Репо | Чат | Топик |
|---|---|---|
| `n8n-io/n8n` | `-1003685199216` «Vibe Business \| Конвейер» | `3410` |
| `anthropics/claude-code` | `-1004473285129` закрытый клуб по нейросетям | `632` |
| `openai/codex` | тот же клуб | `632` |
| `openclaw/openclaw` | тот же клуб | `632` |

Маршруты захардкожены в `DESTINATION` в `watcher.py`. Бот обязан состоять в обоих
чатах — иначе Telegram вернёт 400 и релиз не отправится (state не сдвинется,
следующий прогон попробует снова).

## Что делает

1. GitHub Action бежит по cron `0 9 * * *` (12:00 МСК)
2. Для каждого репо тянет последние 10 релизов через GitHub API
3. Сравнивает с `state.json` (по `release.id`)
4. Новые релизы (не draft, не prerelease) → Claude Haiku 4.5 → русское саммари
5. Шлёт в Telegram через Bot API в топик, закреплённый за этим репо (`DESTINATION`)
6. Обновляет `state.json` и коммитит обратно

## Локальный запуск

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TELEGRAM_BOT_TOKEN=123:ABC...   # токен @vb_regular_alert_bot

pip install -r requirements.txt
python watcher.py
```

## Bootstrap

При пустом `state.json` (`{}`) скрипт **не шлёт всю историю** — он
записывает в state самые свежие релизы каждого репо. Со следующего
запуска шлёт только новое.

Чтобы протестировать отправку:
1. Запусти один раз — bootstrap заполнит state
2. В `state.json` уменьши `last_release_id` для одного репо на 1
3. Закоммить, запусти `workflow_dispatch` руками
4. Должно прилететь сообщение

## Setup на GitHub

```bash
gh repo create sanya8923/release-watcher --public --source=. --push

gh secret set ANTHROPIC_API_KEY --body "sk-ant-..."
gh secret set TELEGRAM_BOT_TOKEN --body "<токен @vb_regular_alert_bot>"

gh workflow run release-watcher.yml
gh run watch
```

## Файлы

- `watcher.py` — основная логика
- `state.json` — last seen release per repo
- `.github/workflows/release-watcher.yml` — cron + workflow_dispatch
- `requirements.txt` — `httpx`, `anthropic`

## Дизайн

См. `docs/plans/2026-05-04-release-watcher-design.md` в основном репо VibeBusiness.
