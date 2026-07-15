import asyncio
import logging
import os
import re

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, ChatJoinRequestHandler, CommandHandler,
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

_agent = BaseAgent(CONFUCIUS_PROMPT, source="telegram")

# user_id -> {"step": "name"/"phone", "name": str, "username": str, "lesson": str}
_signup: dict[int, dict] = {}

# Фразы, которые означают что агент просит имя пользователя
_ASK_NAME_PHRASES = (
    "как вас зовут", "как вас называть", "ваше имя", "напишите своё имя",
    "напишите имя", "оставьте имя", "представьтесь", "как вас звать",
    "скажите своё имя", "напишите ваше имя",
)

# Ключевые слова вопроса о расписании — текст с ними не может быть именем
_SCHEDULE_WORDS = (
    "расписани", "занятия", "занятие", "когда", "во сколько", "время",
    "где", "адрес", "место", "парк", "зал", "записаться", "записат",
    "стоит", "цена", "сколько", "стоимость", "есть ли", "будет ли",
    "прийти", "попасть", "приходить", "онлайн",
)

_SPAM_PATTERNS = re.compile(
    r"(https?://|t\.me/|@[a-zA-Z0-9_]{4,}|"
    r"предлага[юеё]|мои услуги|наши услуги|прайс|прайслист|"
    r"реклам[аеуы]|продви[жг]|раскрутк|накрутк|подписчик|"
    r"зараб[оа]т[аок]|пассивный доход|инвест|крипто|"
    r"скидк[аеуи]|акци[яею]|успей|только сегодня|"
    r"заказ[ыаеу]|оптом|купить|продаю|продаём)",
    re.IGNORECASE,
)


def _is_spam(text: str) -> bool:
    return bool(_SPAM_PATTERNS.search(text))


def _extract_lesson(uid: int) -> str:
    """Извлекает выбранное занятие из истории диалога (ищет строку с временем HH:MM)."""
    history = list(_agent.get_history(uid))
    time_re = re.compile(r'\b\d{1,2}:\d{2}\b')
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            for line in msg.get("content", "").split("\n"):
                if time_re.search(line) and len(line.strip()) < 150:
                    return line.strip()
    return ""


def _looks_like_phone(text: str) -> bool:
    digits = sum(c.isdigit() for c in text)
    return digits >= 7


def _looks_like_name(text: str) -> bool:
    """Проверяет, похож ли текст на имя, а не на вопрос или фразу."""
    t = text.lower().strip()
    # Слишком длинно для имени
    if len(t) > 40:
        return False
    # Содержит ключевые слова вопроса о расписании/занятиях
    if any(kw in t for kw in _SCHEDULE_WORDS):
        return False
    # Содержит вопросительный знак
    if "?" in t:
        return False
    # Содержит цифры (скорее всего вопрос или номер)
    if re.search(r'\d', t):
        return False
    # Очень короткое (одна буква) — не имя
    if len(t) < 2:
        return False
    return True


async def _notify_admins(name: str, phone: str, source: str, username: str, lesson: str = ""):
    if not ADMIN_BOT_TOKEN or not ADMIN_IDS:
        return
    from telegram import Bot
    text = (
        f"🔔 Новая заявка на занятие\n"
        f"👤 Имя: {name}\n"
        f"📱 Телефон: {phone}\n"
        + (f"📅 Занятие: {lesson}\n" if lesson else "")
        + f"📲 Источник: {source}"
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


def _greeting(first_name: str, joined_group: bool = False) -> str:
    intro = f"Спасибо, что вступили в группу @baguazhangspb!\n\n" if joined_group else ""
    return (
        f"Здравствуйте, {first_name}! 👋\n\n"
        f"{intro}"
        "Я Конфуций — помогаю разобраться в практике цигун и ушу на занятиях Сергея в Санкт-Петербурге.\n\n"
        "Готов ответить на любые вопросы — о занятиях, расписании и записи. Чем могу помочь?"
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _agent.clear_history(user.id)
    _signup.pop(user.id, None)
    greeting = _greeting(user.first_name)
    _agent.seed(user.id, greeting)
    await update.message.reply_text("👋")
    await update.message.reply_text(greeting, reply_markup=_main_keyboard())


async def on_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Заявка на вступление в группу @baguazhangspb — одобряем и сразу пишем в личку.
    Telegram разрешает боту первым написать пользователю именно в момент обработки
    его заявки (через user_chat_id), даже если он раньше не начинал диалог с ботом —
    это единственный способ поприветствовать нового подписчика в личке без того, чтобы
    приветствие "прыгало" в самой группе при каждом вступлении."""
    req = update.chat_join_request
    user = req.from_user
    try:
        await ctx.bot.approve_chat_join_request(req.chat.id, user.id)
    except Exception as e:
        logger.error(f"Не удалось одобрить заявку {user.id}: {e}")
        return
    _agent.clear_history(user.id)
    _signup.pop(user.id, None)
    greeting = _greeting(user.first_name, joined_group=True)
    _agent.seed(user.id, greeting)
    try:
        await ctx.bot.send_message(req.user_chat_id, greeting, reply_markup=_main_keyboard())
    except Exception as e:
        logger.warning(f"Не удалось написать в личку новому участнику {user.id}: {e}")


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "sign_up":
        uid = update.effective_user.id
        await ctx.bot.send_chat_action(q.message.chat_id, "typing")
        reply = await _agent.ask(uid, "Хочу записаться на занятие. Покажи ближайшее расписание.")
        # Если агент просит имя — переходим в режим сбора контактов
        if any(p in reply.lower() for p in _ASK_NAME_PHRASES):
            _signup[uid] = {
                "step": "name",
                "username": update.effective_user.username or "",
                "lesson": _extract_lesson(uid),
            }
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

    chat_type = update.effective_chat.type

    if chat_type == "channel":
        return

    # Проверяем группу ДО проверки текста — иначе фото/стикеры/голосовые
    # в группе выходили бы из хендлера раньше, чем сработает редирект в личку
    is_group = chat_type in ("group", "supergroup")
    if is_group:
        # Служебные сообщения о вступлении/выходе — не отвечаем в группе вообще:
        # приветствие новым участникам уходит им в личку через on_join_request
        # (заявки на вступление), а не сюда.
        if msg.new_chat_members or msg.left_chat_member:
            return
        if msg.forward_from_chat or (msg.from_user and msg.from_user.is_bot):
            return
        if _is_spam(msg.text or ""):
            try:
                await ctx.bot.delete_message(msg.chat_id, msg.message_id)
            except Exception as e:
                logger.error(f"Не удалось удалить спам: {e}")
            try:
                await ctx.bot.ban_chat_member(msg.chat_id, uid)
            except Exception as e:
                logger.error(f"Не удалось забанить спамера: {e}")
            return
        bot_me = await ctx.bot.get_me()
        try:
            await msg.reply_text(
                "Отвечу подробнее в личных сообщениях.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Написать Конфуцию", url=f"https://t.me/{bot_me.username}")
                ]]),
            )
        except Exception as e:
            logger.error(f"Ошибка ответа в группе: {e}")
        return

    text = (msg.text or "").strip()
    if not text:
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")

    state = _signup.get(uid)

    # ── Шаг: сбор имени ───────────────────────────────────────────────────────
    if state and state["step"] == "name":
        if text.lower() in ("отмена", "отменить", "стоп"):
            _signup.pop(uid, None)
            await msg.reply_text("Запись отменена.", reply_markup=_main_keyboard())
            return

        if not _looks_like_name(text):
            # Не похоже на имя — отвечаем через агента, потом напоминаем ввести имя
            ai_reply = await _agent.ask(uid, text)
            # Если агент теперь просит имя — он сам об этом скажет, ждём следующего сообщения
            if any(p in ai_reply.lower() for p in _ASK_NAME_PHRASES):
                await msg.reply_text(ai_reply)
            else:
                await msg.reply_text(ai_reply + "\n\nКак вас зовут, чтобы записаться?")
            return

        _signup[uid]["name"] = text
        _signup[uid]["step"] = "phone"
        await msg.reply_text(f"Отлично, {text}! Напишите ваш номер телефона — Сергей свяжется и подтвердит запись:")
        return

    # ── Шаг: сбор телефона ────────────────────────────────────────────────────
    if state and state["step"] == "phone":
        if text.lower() in ("отмена", "отменить", "стоп"):
            _signup.pop(uid, None)
            await msg.reply_text("Запись отменена.", reply_markup=_main_keyboard())
            return

        if not _looks_like_phone(text):
            ai_reply = await _agent.ask(uid, text)
            await msg.reply_text(ai_reply + "\n\nНапишите номер телефона, чтобы завершить запись:")
            return

        saved = _signup.pop(uid)
        name = saved["name"]
        phone = text
        username = update.effective_user.username or ""
        lesson = saved.get("lesson") or _extract_lesson(uid)
        try:
            await asyncio.to_thread(save_booking, name, phone, "telegram", username, str(uid), lesson)
        except Exception as e:
            logger.error(f"Ошибка сохранения заявки: {e}")
        await _notify_admins(name, phone, "Telegram", username, lesson)
        _agent.clear_history(uid)
        await msg.reply_text(
            f"Записал вас, {name}. Сергей свяжется в ближайшее время.",
            reply_markup=_main_keyboard(),
        )
        return

    # ── Обычный диалог через агента ───────────────────────────────────────────
    reply = await _agent.ask(uid, text)

    # Если агент в ответе просит имя — переходим в режим сбора контактов
    if not _signup.get(uid) and any(p in reply.lower() for p in _ASK_NAME_PHRASES):
        _signup[uid] = {
            "step": "name",
            "username": update.effective_user.username or "",
            "lesson": _extract_lesson(uid),
        }

    await msg.reply_text(reply, reply_markup=_main_keyboard())


def main():
    if not CLIENT_BOT_TOKEN:
        logger.error("TELEGRAM_CLIENT_TOKEN не задан")
        return

    app = Application.builder().token(CLIENT_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    logger.info("Client bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio as _asyncio
    _asyncio.set_event_loop(_asyncio.new_event_loop())
    main()
