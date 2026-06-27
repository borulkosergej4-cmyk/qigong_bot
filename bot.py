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
from data.database import save_booking

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CLIENT_BOT_TOKEN = os.getenv("TELEGRAM_CLIENT_TOKEN", "").strip()
ADMIN_BOT_TOKEN  = os.getenv("ADMIN_BOT_TOKEN", "").strip()
_raw_ids = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = [int(x) for x in _raw_ids.split(",") if x.strip().isdigit()]

_agent = BaseAgent(CONFUCIUS_PROMPT)

# user_id -> {"step": "name"/"phone", "name": str, "username": str}
_signup: dict[int, dict] = {}

_SIGNUP_KEYWORDS = (
    "записаться", "запишите", "запиши меня", "хочу попасть",
    "как попасть", "хочу прийти", "как записаться", "хочу записаться",
    "хотел бы записаться", "хотела бы записаться", "можно записаться",
    "как мне записаться", "запись к сергею", "попасть на занятие",
    "прийти на занятие", "хочу на занятие", "запись на занятие",
)


def _wants_signup(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _SIGNUP_KEYWORDS)


def _looks_like_phone(text: str) -> bool:
    digits = sum(c.isdigit() for c in text)
    return digits >= 7


async def _notify_admins(name: str, phone: str, source: str, username: str):
    if not ADMIN_BOT_TOKEN or not ADMIN_IDS:
        return
    from telegram import Bot
    text = (
        f"🔔 Новая заявка на занятие\n"
        f"👤 Имя: {name}\n"
        f"📱 Телефон: {phone}\n"
        f"📲 Источник: {source}"
        + (f"\n🆔 @{username}" if username else "")
    )
    bot = Bot(token=ADMIN_BOT_TOKEN)
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text)
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа {aid}: {e}")


def _main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Записаться на занятие", callback_data="sign_up")],
        [InlineKeyboardButton("💬 Задать вопрос",         callback_data="ask_question")],
        [InlineKeyboardButton("📖 О канале",              callback_data="about_channel")],
        [InlineKeyboardButton("🔗 Подписные площадки",   callback_data="subscriptions")],
    ])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _agent.clear_history(user.id)
    _signup.pop(user.id, None)
    greeting = (
        f"Здравствуйте, {user.first_name}! 👋\n\n"
        "Я Конфуций — помогаю разобраться в практике цигун и ушу у Сергея в Санкт-Петербурге.\n\n"
        "Готов ответить на любые вопросы — о занятиях, расписании и записи. Чем могу помочь?"
    )
    # Сохраняем приветствие в историю — чтобы AI не повторял представление
    _agent.seed(user.id, greeting)
    await update.message.reply_text("👋")
    await update.message.reply_text(greeting, reply_markup=_main_keyboard())


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "sign_up":
        # Сначала показываем расписание через агента, не сразу просим контакты
        uid = update.effective_user.id
        await q.answer()
        await ctx.bot.send_chat_action(q.message.chat_id, "typing")
        reply = await _agent.ask(uid, "Хочу записаться на занятие. Покажи ближайшее расписание.")
        await q.message.reply_text(reply, reply_markup=_main_keyboard())
        return

    elif data == "ask_question":
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
            "Boosty — онлайн-занятия с Сергеем, видеоуроки и материалы для подписчиков.\n\n"
            "VK Донат — поддержать канал, подписки с доступом к видеоурокам.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Boosty — онлайн-занятия и видеоуроки", url="https://boosty.to/s.borulko")],
                [InlineKeyboardButton("VK Донат — поддержать канал", url="https://vk.com/donut/club162151764")],
                [InlineKeyboardButton("◀ Назад", callback_data="back_main")],
            ]),
        )

    elif data == "back_main":
        await q.edit_message_text(
            "Чем могу помочь?",
            reply_markup=_main_keyboard(),
        )


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    uid = update.effective_user.id
    text = (msg.text or "").strip()
    if not text:
        return

    chat_type = update.effective_chat.type

    # Посты из канала — игнорируем полностью
    if chat_type == "channel":
        return

    is_group = chat_type in ("group", "supergroup")

    if is_group:
        if msg.forward_from_chat or (msg.from_user and msg.from_user.is_bot):
            return
        bot_me = await ctx.bot.get_me()
        bot_username = bot_me.username
        # В группе — на любое сообщение от живого пользователя переводим в личку
        try:
            await msg.reply_text(
                "Отвечу подробнее в личных сообщениях.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Написать Конфуцию",
                        url=f"https://t.me/{bot_username}",
                    )
                ]]),
            )
        except Exception as e:
            logger.error(f"Ошибка ответа в группе: {e}")
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")

    # ── Флоу записи ───────────────────────────────────────────────────────────
    state = _signup.get(uid)

    if state and state["step"] == "name":
        if text.lower() in ("отмена", "отменить", "стоп"):
            _signup.pop(uid, None)
            await msg.reply_text("Запись отменена.", reply_markup=_main_keyboard())
            return
        if "?" in text or len(text) > 60 or any(
            text.lower().startswith(w) for w in ("как", "где", "когда", "что", "почему", "сколько")
        ):
            ai_reply = await _agent.ask(uid, text)
            await msg.reply_text(
                ai_reply + "\n\nЧтобы записаться — напишите ваше имя:"
            )
            return
        _signup[uid]["name"] = text
        _signup[uid]["step"] = "phone"
        await msg.reply_text("Спасибо. Теперь напишите ваш номер телефона:")
        return

    if state and state["step"] == "phone":
        if text.lower() in ("отмена", "отменить", "стоп"):
            _signup.pop(uid, None)
            await msg.reply_text("Запись отменена.", reply_markup=_main_keyboard())
            return
        if not _looks_like_phone(text):
            # Похоже на вопрос, а не на номер — отвечаем и просим номер ещё раз
            ai_reply = await _agent.ask(uid, text)
            await msg.reply_text(
                ai_reply + "\n\nНапишите ваш номер телефона, чтобы завершить запись:"
            )
            return
        name = _signup.pop(uid)["name"]
        phone = text
        username = update.effective_user.username or ""
        try:
            await asyncio.to_thread(
                save_booking, name, phone, "telegram", username, str(uid)
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения заявки: {e}")
        await _notify_admins(name, phone, "Telegram", username)
        _agent.clear_history(uid)
        await msg.reply_text(
            f"Записал вас, {name}. Сергей свяжется с вами в ближайшее время.",
            reply_markup=_main_keyboard(),
        )
        return

    if _wants_signup(text):
        _signup[uid] = {"step": "name", "username": update.effective_user.username or ""}
        await msg.reply_text(
            "Оставьте имя и номер телефона — Сергей свяжется с вами и подберёт удобное время.\n\n"
            "Как вас зовут?"
        )
        return

    # ── Обычный диалог с Конфуцием ────────────────────────────────────────────
    reply = await _agent.ask(uid, text)
    await msg.reply_text(reply, reply_markup=_main_keyboard())


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
