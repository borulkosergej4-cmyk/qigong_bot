"""
Запускает все сервисы параллельно:
1. dashboard (FastAPI) — Railway ждёт открытый порт
2. admin_bot (Telegram) — управление контентом (webhook на Railway, polling локально)
3. bot (Telegram) — Конфуций отвечает клиентам
4. vk_bot (VK) — Конфуций отвечает в VK
"""
import asyncio
import logging
import os
import secrets as _secrets

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def run_dashboard():
    from dashboard import app
    port = int(os.getenv("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info(f"Dashboard: порт {port}")
    await server.serve()


async def run_admin_bot():
    import app_state
    from admin_bot import Application, ADMIN_BOT_TOKEN, post_init, cmd_start, on_callback, on_message
    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters

    if not ADMIN_BOT_TOKEN:
        logger.error("ADMIN_BOT_TOKEN не задан — admin_bot не запущен")
        return

    telegram_app = (
        Application.builder()
        .token(ADMIN_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(on_callback))
    telegram_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    app_state.admin_application = telegram_app
    app_state.webhook_secret = _secrets.token_hex(16)

    await telegram_app.initialize()
    await telegram_app.start()

    RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if RAILWAY_DOMAIN:
        webhook_url = f"https://{RAILWAY_DOMAIN}/webhook/admin_bot"
        await telegram_app.bot.set_webhook(
            url=webhook_url,
            secret_token=app_state.webhook_secret,
            drop_pending_updates=True,
        )
        logger.info(f"Admin bot: webhook → {webhook_url}")
    else:
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Admin bot: polling mode (локально)")

    await asyncio.Event().wait()


async def run_client_bot():
    import bot as client_bot
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

    token = (
        os.getenv("TELEGRAM_CLIENT_TOKEN", "").strip()
        or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    if not token:
        logger.warning("TELEGRAM_CLIENT_TOKEN / TELEGRAM_BOT_TOKEN не заданы — client bot не запущен")
        return

    app = (
        Application.builder()
        .token(token)
        .build()
    )
    app.add_handler(CommandHandler("start", client_bot.cmd_start))
    app.add_handler(CallbackQueryHandler(client_bot.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, client_bot.on_message))

    logger.info("Client bot: запускаю polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


async def run_vk_bot():
    from vk_bot import run
    await run()


async def _safe_run(name: str, coro):
    try:
        await coro
    except Exception as e:
        logger.error(f"Сервис '{name}' упал: {e}", exc_info=True)


async def main():
    await asyncio.gather(
        _safe_run("dashboard", run_dashboard()),
        _safe_run("admin_bot", run_admin_bot()),
        _safe_run("client_bot", run_client_bot()),
        _safe_run("vk_bot", run_vk_bot()),
    )


if __name__ == "__main__":
    asyncio.run(main())
