import asyncio
import logging
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

try:
    import httpx._utils as _httpx_utils
    _httpx_utils.getproxies = lambda: {}
except Exception:
    pass

from agents.base_agent import BaseAgent
from data.knowledge import CONFUCIUS_PROMPT
from data.database import save_booking

logger = logging.getLogger(__name__)

VK_TOKEN        = os.getenv("VK_TOKEN", "").strip()
VK_GROUP_ID     = os.getenv("VK_GROUP_ID", "").strip()
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "").strip()
_raw_ids = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = [int(x) for x in _raw_ids.split(",") if x.strip().isdigit()]

_agent = BaseAgent(CONFUCIUS_PROMPT, source="vk")

# vk_user_id -> {"step": "name"/"phone", "name": str}
_signup: dict[int, dict] = {}

# Фразы, которые означают что агент просит имя пользователя (см. CONFUCIUS_PROMPT,
# Шаг 2: "Записываю вас на [занятие]. Как вас зовут?") — тот же принцип, что в bot.py
_ASK_NAME_PHRASES = (
    "как вас зовут", "как вас называть", "ваше имя", "напишите своё имя",
    "напишите имя", "оставьте имя", "представьтесь", "как вас звать",
    "скажите своё имя", "напишите ваше имя",
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


def _looks_like_phone(text: str) -> bool:
    return sum(c.isdigit() for c in text) >= 7


def _extract_lesson(user_id: int) -> str:
    """Извлекает выбранное занятие из истории диалога (ищет строку с временем HH:MM)."""
    import re as _re
    history = list(_agent.get_history(user_id))
    time_re = _re.compile(r'\b\d{1,2}:\d{2}\b')
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            for line in msg.get("content", "").split("\n"):
                if time_re.search(line) and len(line.strip()) < 150:
                    return line.strip()
    return ""


async def _notify_admins(name: str, phone: str, lesson: str = ""):
    if not ADMIN_BOT_TOKEN or not ADMIN_IDS:
        return
    from telegram import Bot
    text = (
        f"🔔 Новая заявка на занятие\n"
        f"👤 Имя: {name}\n"
        f"📱 Телефон: {phone}\n"
        + (f"📅 Занятие: {lesson}\n" if lesson else "")
        + f"📲 Источник: VK"
    )
    bot = Bot(token=ADMIN_BOT_TOKEN)
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text)
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа {aid}: {e}")

_VK_API = "https://api.vk.com/method"
_VK_VERSION = "5.131"


async def _send_message(user_id: int, text: str):
    import random
    async with httpx.AsyncClient(timeout=15, trust_env=False) as cl:
        await cl.post(
            f"{_VK_API}/messages.send",
            data={
                "user_id": user_id,
                "message": text,
                "random_id": random.randint(0, 2**31),
                "access_token": VK_TOKEN,
                "v": _VK_VERSION,
            },
        )


async def _poll():
    async with httpx.AsyncClient(timeout=30, trust_env=False) as cl:
        r = await cl.get(
            f"{_VK_API}/groups.getLongPollServer",
            params={"group_id": VK_GROUP_ID, "access_token": VK_TOKEN, "v": _VK_VERSION},
        )
        rj = r.json()
        if "error" in rj:
            err = rj["error"]
            raise RuntimeError(
                f"VK getLongPollServer ошибка {err.get('error_code')}: {err.get('error_msg')}"
            )
        data = rj["response"]
        server, key, ts = data["server"], data["key"], data["ts"]

    logger.info("VK long poll запущен")
    async with httpx.AsyncClient(timeout=35, trust_env=False) as cl:
        while True:
            try:
                resp = await cl.get(
                    server,
                    params={"act": "a_check", "key": key, "ts": ts, "wait": 25},
                )
                result = resp.json()

                if "failed" in result:
                    logger.warning(f"VK long poll failed={result['failed']}, перезапускаю")
                    return

                ts = result["ts"]
                for event in result.get("updates", []):
                    if event.get("type") != "message_new":
                        continue
                    msg = event["object"]["message"]
                    user_id = msg.get("from_id")
                    text = (msg.get("text") or "").strip()
                    if not text or user_id <= 0:
                        continue
                    asyncio.create_task(_handle(user_id, text))

            except Exception as e:
                logger.warning(f"VK poll error: {e}")
                await asyncio.sleep(5)


async def _handle(user_id: int, text: str):
    try:
        if _is_spam(text) and not _signup.get(user_id):
            logger.warning(f"VK: похоже на спам от {user_id}, игнорирую: {text[:80]!r}")
            return

        state = _signup.get(user_id)

        if state and state["step"] == "name":
            if text.lower() in ("отмена", "отменить", "стоп"):
                _signup.pop(user_id, None)
                await _send_message(user_id, "Запись отменена.")
                return
            if "?" in text or len(text) > 60 or any(
                text.lower().startswith(w) for w in ("как", "где", "когда", "что", "почему", "сколько")
            ):
                ai_reply = await _agent.ask(user_id, text)
                await _send_message(user_id,
                    ai_reply + "\n\nЧтобы записаться — напишите ваше имя:")
                return
            _signup[user_id]["name"] = text
            _signup[user_id]["step"] = "phone"
            await _send_message(user_id, "Спасибо. Теперь напишите ваш номер телефона:")
            return

        if state and state["step"] == "phone":
            if text.lower() in ("отмена", "отменить", "стоп"):
                _signup.pop(user_id, None)
                await _send_message(user_id, "Запись отменена.")
                return
            if not _looks_like_phone(text):
                ai_reply = await _agent.ask(user_id, text)
                await _send_message(user_id,
                    ai_reply + "\n\nНапишите ваш номер телефона, чтобы завершить запись:")
                return
            saved = _signup.pop(user_id)
            name = saved["name"]
            phone = text
            lesson = saved.get("lesson") or _extract_lesson(user_id)
            try:
                await asyncio.to_thread(
                    save_booking, name, phone, "vk", str(user_id), str(user_id), lesson
                )
            except Exception as e:
                logger.error(f"Ошибка сохранения заявки VK: {e}")
            await _notify_admins(name, phone, lesson)
            _agent.clear_history(user_id)
            await _send_message(user_id,
                f"Записал вас, {name}. Сергей свяжется с вами в ближайшее время.")
            return

        # Обычный диалог через агента — включая просьбы записаться: согласно
        # CONFUCIUS_PROMPT агент сначала покажет расписание и уточнит день/время,
        # и лишь когда сам спросит "Как вас зовут?" — переходим к сбору контактов
        # (тот же принцип, что в bot.py для Telegram)
        reply = await _agent.ask(user_id, text)
        if not _signup.get(user_id) and any(p in reply.lower() for p in _ASK_NAME_PHRASES):
            _signup[user_id] = {"step": "name", "lesson": _extract_lesson(user_id)}
        await _send_message(user_id, reply)

    except Exception as e:
        logger.error(f"VK handle error: {e}")


async def run():
    if not VK_TOKEN or not VK_GROUP_ID:
        logger.warning("VK_TOKEN или VK_GROUP_ID не заданы — VK-бот не запущен")
        return
    while True:
        try:
            await _poll()
        except Exception as e:
            logger.error(f"VK poll restart: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    import asyncio as _asyncio
    logging.basicConfig(level=logging.INFO)
    _asyncio.run(run())
