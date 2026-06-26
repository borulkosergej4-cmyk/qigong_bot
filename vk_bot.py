import asyncio
import logging
import os

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

_agent = BaseAgent(CONFUCIUS_PROMPT)

# vk_user_id -> {"step": "name"/"phone", "name": str}
_signup: dict[int, dict] = {}

_SIGNUP_KEYWORDS = (
    "записаться", "запишите", "хочу записаться", "как записаться",
    "запись на", "хотел бы записаться", "можно записаться",
    "хочу попасть", "как попасть", "попасть на занятие",
)


def _wants_signup(text: str) -> bool:
    return any(kw in text.lower() for kw in _SIGNUP_KEYWORDS)


async def _notify_admins(name: str, phone: str):
    if not ADMIN_BOT_TOKEN or not ADMIN_IDS:
        return
    from telegram import Bot
    text = (
        f"🔔 Новая заявка на занятие\n"
        f"👤 Имя: {name}\n"
        f"📱 Телефон: {phone}\n"
        f"📲 Источник: VK"
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
                    await _poll()
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
        state = _signup.get(user_id)

        if state and state["step"] == "name":
            _signup[user_id]["name"] = text
            _signup[user_id]["step"] = "phone"
            await _send_message(user_id, "Спасибо. Теперь напишите ваш номер телефона:")
            return

        if state and state["step"] == "phone":
            name = _signup.pop(user_id)["name"]
            phone = text
            save_booking(name, phone, "vk", str(user_id))
            await _notify_admins(name, phone)
            _agent.clear_history(user_id)
            await _send_message(user_id,
                f"Записал вас, {name}. Тренер свяжется с вами в ближайшее время.")
            return

        if _wants_signup(text):
            _signup[user_id] = {"step": "name"}
            await _send_message(user_id, "Хорошо, запишу вас. Как вас зовут?")
            return

        reply = await _agent.ask(user_id, text)
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
