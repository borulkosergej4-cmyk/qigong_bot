"""
Запускает все сервисы параллельно:
1. dashboard (FastAPI) — Railway ждёт открытый порт
2. admin_bot (Telegram)
"""
import asyncio
import logging
import os

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
    from admin_bot import Application, ADMIN_BOT_TOKEN, post_init, cmd_start, on_callback, on_message
    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters

    if not ADMIN_BOT_TOKEN:
        logger.error("ADMIN_BOT_TOKEN не задан — admin_bot не запущен")
        return

    app = (
        Application.builder()
        .token(ADMIN_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    logger.info("Admin bot: запускаю polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


async def main():
    await asyncio.gather(
        run_dashboard(),
        run_admin_bot(),
    )


if __name__ == "__main__":
    asyncio.run(main())
