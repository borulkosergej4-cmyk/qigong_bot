# CLAUDE.md

Руководство по кодовой базе для Claude Code.

## Что это

Бот-администратор для Telegram-канала и группы VK, посвящённых китайской гимнастике **«Целительный цигун и ушу»**.

Функции:
- Генерация и публикация постов (Telegram-канал + VK)
- Создание Reels/клипов
- Контент-план
- Управление ссылками на платные подписные площадки (Boosty, VK Donut и др.)
- Отложенная публикация

Деплой: **Railway** (автодеплой при пуше в `main`).
База данных: **Neon PostgreSQL**.

## Команды

```bash
pip install -r requirements.txt
python run_all.py        # dashboard + admin bot
python admin_bot.py      # только admin bot
```

## Архитектура

Два сервиса в одном процессе (`run_all.py`):
1. **dashboard** — FastAPI на порту `$PORT`
2. **admin_bot.py** — Telegram-бот для администратора

## Переменные окружения

| Переменная | Описание |
|---|---|
| `ADMIN_BOT_TOKEN` | Токен Telegram-бота |
| `ADMIN_IDS` | ID администраторов через запятую |
| `TG_CHANNEL` | Username Telegram-канала (напр. `@qigong_channel`) |
| `VK_TOKEN` | Групповой токен VK |
| `VK_USER_TOKEN` | Пользовательский токен VK (для загрузки фото) |
| `VK_GROUP_ID` | ID группы VK (число) |
| `ANTHROPIC_API_KEY` | Ключ Anthropic API (Claude Haiku) |
| `OPENROUTER_API_KEY` | Ключ OpenRouter API |
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `UNSPLASH_ACCESS_KEY` | Ключ Unsplash API для фото |
| `DASHBOARD_PASSWORD` | Пароль дашборда (дефолт: `qigong`) |
| `DASHBOARD_USERNAME` | Логин дашборда (дефолт: `admin`) |

## База данных

| Таблица | Назначение |
|---|---|
| `generated_posts` | Посты (опубликованные и запланированные) |
| `content_plan` | Контент-план по месяцам |
| `subscription_links` | Ссылки на платные площадки |
| `saved_bg_videos` | Видео-фоны для Reels (BYTEA) |
| `saved_bg_audios` | Аудио-треки для Reels (BYTEA) |
| `bot_config` | Логотип и прочие настройки (BYTEA) |
