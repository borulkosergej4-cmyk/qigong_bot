import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

load_dotenv()

try:
    import httpx._utils as _httpx_utils
    _httpx_utils.getproxies = lambda: {}
except Exception:
    pass
for _pv in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(_pv, None)

from agents.base_agent import BaseAgent
from data.knowledge import CONFUCIUS_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CLIENT_BOT_TOKEN = os.getenv("TELEGRAM_CLIENT_TOKEN", "").strip()

_agent = BaseAgent(CONFUCIUS_PROMPT)


def _main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🙋 Задать вопрос",       callback_data="ask_question")],
        [InlineKeyboardButton("📖 О канале",             callback_data="about_channel")],
        [InlineKeyboardButton("🔗 Подписные площадки",  callback_data="subscriptions")],
    ])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _agent.clear_history(update.effective_user.id)
    await update.message.reply_text(
        "Приветствую. Я Конфуций — помогу разобраться в практике цигун и ушу.\n\n"
        "Можешь задать любой вопрос или выбрать раздел:",
        reply_markup=_main_keyboard(),
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "ask_question":
        await q.edit_message_text(
            "Задай свой вопрос — отвечу.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ Назад", callback_data="back_main")],
            ]),
        )

    elif data == "about_channel":
        await q.edit_message_text(
            "Канал @baguazhangspb — практика цигун и ушу в Санкт-Петербурге.\n\n"
            "Публикуем техники для самостоятельной практики, разбираем ошибки новичков, "
            "анонсируем занятия и онлайн-курсы.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ Назад", callback_data="back_main")],
            ]),
        )

    elif data == "subscriptions":
        await q.edit_message_text(
            "Платные материалы и поддержка канала:\n\n"
            "Ссылки на Boosty, VK Donut и другие площадки — уточни у администратора канала.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ Назад", callback_data="back_main")],
            ]),
        )

    elif data == "back_main":
        await q.edit_message_text(
            "Чем могу помочь?",
            reply_markup=_main_keyboard(),
        )


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    reply = await _agent.ask(uid, text)
    await update.message.reply_text(reply, reply_markup=_main_keyboard())


def main():
    if not CLIENT_BOT_TOKEN:
        logger.error("TELEGRAM_CLIENT_TOKEN не задан")
        return

    app = (
        Application.builder()
        .token(CLIENT_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info("Client bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio as _asyncio
    _asyncio.set_event_loop(_asyncio.new_event_loop())
    main()
