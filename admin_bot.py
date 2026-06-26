"""
Бот-администратор для канала «Целительный цигун и ушу».
Функции: генерация постов, Reels, контент-план, ссылки на подписки, медиа.
"""
import asyncio
import io
import json
import logging
import os
import random
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Отключаем системный SOCKS-прокси — httpx импортирует getproxies напрямую
try:
    import httpx._utils as _httpx_utils
    _httpx_utils.getproxies = lambda: {}
except Exception:
    pass
for _pv in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(_pv, None)

from telegram import (
    Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

from data.database import (
    approve_content_plan, get_content_plan, get_posts_history,
    get_scheduled_posts, init_db, mark_post_published, save_post,
    save_content_plan, get_subscription_links, add_subscription_link,
    delete_subscription_link, save_bg_video, get_bg_videos, delete_bg_video,
    save_bg_audio, get_bg_audios, delete_bg_audio, save_logo, get_logo,
)
from data.knowledge import CONTENT_PLAN_SYSTEM_PROMPT, POST_SYSTEM_PROMPT
from py_render import (
    clear_logo_cache, render_announcement, render_motivation, render_promo,
    _extract_bg_audio,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Конфигурация ──────────────────────────────────────────────────────────────
ADMIN_BOT_TOKEN  = os.getenv("ADMIN_BOT_TOKEN", "").strip()
_raw_ids = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS        = [int(x) for x in _raw_ids.split(",") if x.strip().isdigit()]
TG_CHANNEL       = os.getenv("TG_CHANNEL", "@your_channel").strip()
VK_TOKEN         = os.getenv("VK_TOKEN", "").strip()
VK_USER_TOKEN    = os.getenv("VK_USER_TOKEN", "").strip()
VK_GROUP_ID      = os.getenv("VK_GROUP_ID", "").strip()      # числовой ID группы VK
UNSPLASH_KEY     = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()

_OPENROUTER_KEY  = os.getenv("OPENROUTER_API_KEY", "").strip()
_ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY", "").strip()

_OPENROUTER_MODEL   = "google/gemma-2-27b-it:free"
_OPENROUTER_FALLBACK = "meta-llama/llama-3.3-70b-instruct:free"

_openrouter = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=_OPENROUTER_KEY or "not-set",
    max_retries=0,
    http_client=httpx.Client(trust_env=False),
)

# ── Состояния пользователя ────────────────────────────────────────────────────
USER_STATE:     dict[int, str]  = {}
USER_GENERATED: dict[int, dict] = {}
CONTENT_PLANS:  dict[int, list] = {}

# ── Счётчик ошибок публикации (сбрасывается при рестарте) ────────────────────
_sched_failures: dict[int, int] = {}
_SCHED_MAX_RETRIES = 3

# ── Директории медиа ──────────────────────────────────────────────────────────
VIDEOS_DIR = Path("videos")
AUDIO_DIR  = Path("audio")
PHOTOS_DIR = Path("photos")
STATIC_DIR = Path("static")
for _d in (VIDEOS_DIR, AUDIO_DIR, PHOTOS_DIR, STATIC_DIR):
    _d.mkdir(exist_ok=True)

LOGO_PATH = STATIC_DIR / "logo.png"


# ═══════════════════════════════════════════════════════════════════════════════
# AI-генерация текста
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_text(user_prompt: str, system: str | None = None) -> str:
    sys = system or POST_SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": sys},
        {"role": "user",   "content": user_prompt},
    ]
    # 1. Anthropic Claude Haiku
    if _ANTHROPIC_KEY:
        try:
            import anthropic as _sdk
            cl = _sdk.Anthropic(api_key=_ANTHROPIC_KEY)
            resp = cl.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1200,
                system=sys,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = resp.content[0].text
            if text:
                return text
        except Exception as e:
            logger.warning(f"Claude error: {e}")

    # 2. OpenRouter
    for model in (_OPENROUTER_MODEL, _OPENROUTER_FALLBACK):
        try:
            resp = _openrouter.chat.completions.create(
                model=model, messages=messages, max_tokens=1200,
            )
            text = resp.choices[0].message.content
            if text:
                return text
        except Exception as e:
            logger.warning(f"OpenRouter/{model} error: {e}")

    raise RuntimeError("Все AI-провайдеры недоступны")


# ═══════════════════════════════════════════════════════════════════════════════
# VK публикация
# ═══════════════════════════════════════════════════════════════════════════════

async def _vk_post(text: str, photo_bytes: bytes | None = None) -> tuple[bool, str]:
    if not VK_TOKEN or not VK_GROUP_ID:
        return False, "VK_TOKEN или VK_GROUP_ID не заданы"
    try:
        attachments = ""
        if photo_bytes and VK_USER_TOKEN:
            async with httpx.AsyncClient(timeout=30) as cl:
                r = await cl.get(
                    "https://api.vk.com/method/photos.getWallUploadServer",
                    params={"group_id": VK_GROUP_ID, "access_token": VK_USER_TOKEN, "v": "5.131"},
                )
                rj = r.json()
                if "error" in rj:
                    return False, f"photos.getWallUploadServer: {rj['error'].get('error_msg', rj['error'])}"
                upload_url = rj["response"]["upload_url"]
                up = await cl.post(upload_url, files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")})
                res = up.json()
                sv = await cl.post(
                    "https://api.vk.com/method/photos.saveWallPhoto",
                    data={
                        "group_id": VK_GROUP_ID,
                        "photo": res["photo"], "server": res["server"], "hash": res["hash"],
                        "access_token": VK_USER_TOKEN, "v": "5.131",
                    },
                )
                svj = sv.json()
                if "error" in svj:
                    return False, f"photos.saveWallPhoto: {svj['error'].get('error_msg', svj['error'])}"
                p = svj["response"][0]
                attachments = f"photo{p['owner_id']}_{p['id']}"

        async with httpx.AsyncClient(timeout=30) as cl:
            r = await cl.post(
                "https://api.vk.com/method/wall.post",
                data={
                    "owner_id": f"-{VK_GROUP_ID}",
                    "message": text,
                    "attachments": attachments,
                    "from_group": 1,
                    "access_token": VK_TOKEN,
                    "v": "5.131",
                },
            )
            result = r.json()
            if "error" in result:
                err = result["error"]
                msg = f"wall.post ошибка {err.get('error_code','?')}: {err.get('error_msg','?')}"
                logger.error(f"VK {msg}")
                return False, msg
            return True, ""
    except Exception as e:
        logger.error(f"VK post error: {e}")
        return False, str(e)


async def _vk_clip(video_path: str) -> bool:
    if not VK_TOKEN or not VK_GROUP_ID:
        return False
    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            r = await cl.get(
                "https://api.vk.com/method/video.save",
                params={
                    "group_id": VK_GROUP_ID, "name": "Reel",
                    "is_private": 0, "wallpost": 1,
                    "access_token": VK_TOKEN, "v": "5.131",
                },
            )
            upload_url = r.json()["response"]["upload_url"]
            with open(video_path, "rb") as f:
                await cl.post(upload_url, files={"video_file": f})
        return True
    except Exception as e:
        logger.error(f"VK clip error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Фото для поста
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_photo(query: str = "qigong tai chi") -> bytes | None:
    local = list(PHOTOS_DIR.glob("*.jpg")) + list(PHOTOS_DIR.glob("*.png"))
    if local:
        return Path(random.choice(local)).read_bytes()
    if UNSPLASH_KEY:
        try:
            async with httpx.AsyncClient(timeout=15) as cl:
                r = await cl.get(
                    "https://api.unsplash.com/photos/random",
                    params={"query": query, "orientation": "squarish"},
                    headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"},
                )
                url = r.json()["urls"]["regular"]
                img = await cl.get(url)
                return img.content
        except Exception as e:
            logger.warning(f"Unsplash error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Автопубликация по расписанию
# ═══════════════════════════════════════════════════════════════════════════════

async def _publish_any(bot: Bot, post_id: int, platform: str, text: str,
                       photo_bytes: bytes | None) -> bool:
    ok = True
    if platform in ("post_tg", "post_both"):
        try:
            if photo_bytes:
                await bot.send_photo(chat_id=TG_CHANNEL, photo=photo_bytes, caption=text)
            else:
                await bot.send_message(chat_id=TG_CHANNEL, text=text)
        except Exception as e:
            logger.error(f"TG publish error post {post_id}: {e}")
            ok = False
    if platform in ("post_vk", "post_both"):
        vk_ok, _ = await _vk_post(text, photo_bytes)
        if not vk_ok:
            ok = False
    return ok


async def check_and_publish_scheduled(bot: Bot):
    try:
        posts = get_scheduled_posts()
    except Exception as e:
        logger.error(f"get_scheduled_posts error: {e}")
        return
    if not posts:
        return
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    for post_id, platform, text, photo_bytes, scheduled_at in posts:
        if scheduled_at and scheduled_at <= now_utc:
            failures = _sched_failures.get(post_id, 0)
            if failures >= _SCHED_MAX_RETRIES:
                continue
            pb = bytes(photo_bytes) if photo_bytes else None
            ok = await _publish_any(bot, post_id, platform, text, pb)
            if ok:
                mark_post_published(post_id)
                _sched_failures.pop(post_id, None)
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id,
                            f"✅ Пост #{post_id} опубликован автоматически.")
                    except Exception:
                        pass
            else:
                _sched_failures[post_id] = failures + 1
                if failures == 0:
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id,
                                f"⚠️ Пост #{post_id}: ошибка публикации. Повторю попытку.")
                        except Exception:
                            pass
                elif failures + 1 >= _SCHED_MAX_RETRIES:
                    mark_post_published(post_id)
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id,
                                f"❌ Пост #{post_id} не удалось опубликовать после "
                                f"{_SCHED_MAX_RETRIES} попыток. Отправь вручную:\n\n{text}")
                        except Exception:
                            pass


async def _scheduled_post_loop(bot: Bot) -> None:
    await asyncio.sleep(10)
    logger.info("_scheduled_post_loop: запущен, интервал 30 сек")
    iteration = 0
    while True:
        try:
            await check_and_publish_scheduled(bot)
            iteration += 1
            if iteration % 10 == 0:
                logger.info(f"_scheduled_post_loop alive: итерация {iteration}")
        except asyncio.CancelledError:
            logger.info("_scheduled_post_loop: завершение (CancelledError)")
            return
        except Exception as e:
            logger.error(f"_scheduled_post_loop error: {e}", exc_info=True)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("_scheduled_post_loop: sleep прерван, завершение")
            return


# ═══════════════════════════════════════════════════════════════════════════════
# Восстановление медиа после рестарта Railway
# ═══════════════════════════════════════════════════════════════════════════════

async def _restore_media():
    try:
        for vid_id, name, data in get_bg_videos():
            p = VIDEOS_DIR / name
            if not p.exists():
                p.write_bytes(bytes(data))
        logger.info("Видео-фоны восстановлены")
    except Exception as e:
        logger.warning(f"Ошибка восстановления видео: {e}")

    try:
        for aud_id, name, data in get_bg_audios():
            p = AUDIO_DIR / name
            if not p.exists():
                p.write_bytes(bytes(data))
        logger.info("Аудио-треки восстановлены")
    except Exception as e:
        logger.warning(f"Ошибка восстановления аудио: {e}")

    try:
        logo_data = get_logo()
        if logo_data and not LOGO_PATH.exists():
            LOGO_PATH.write_bytes(logo_data)
            clear_logo_cache()
            logger.info("Логотип восстановлен")
    except Exception as e:
        logger.warning(f"Ошибка восстановления логотипа: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Вспомогательные клавиатуры
# ═══════════════════════════════════════════════════════════════════════════════

def _main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Создать пост",      callback_data="create_post")],
        [InlineKeyboardButton("🎬 Создать Reel",       callback_data="create_reel")],
        [InlineKeyboardButton("📅 Контент-план",       callback_data="content_plan")],
        [InlineKeyboardButton("🔗 Подписные ссылки",   callback_data="subscriptions")],
        [InlineKeyboardButton("🎥 Медиа",              callback_data="media_menu")],
        [InlineKeyboardButton("📋 История постов",     callback_data="posts_history")],
    ])

def _platform_keyboard(prefix: str = "platform"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Telegram",    callback_data=f"{prefix}_tg")],
        [InlineKeyboardButton("ВКонтакте",  callback_data=f"{prefix}_vk")],
        [InlineKeyboardButton("Обе",        callback_data=f"{prefix}_both")],
        [InlineKeyboardButton("◀ Назад",    callback_data="main_menu")],
    ])

def _back_keyboard(cb: str = "main_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Назад", callback_data=cb)]])


# ═══════════════════════════════════════════════════════════════════════════════
# Проверка доступа
# ═══════════════════════════════════════════════════════════════════════════════

def _is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


# ═══════════════════════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "Панель управления каналом 🌿 Целительный цигун и ушу",
        reply_markup=_main_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Callback-роутер
# ═══════════════════════════════════════════════════════════════════════════════

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not _is_admin(uid):
        return
    data = q.data

    # ── Главное меню ──────────────────────────────────────────────────────────
    if data == "main_menu":
        USER_STATE.pop(uid, None)
        USER_GENERATED.pop(uid, None)
        await q.edit_message_text("Панель управления", reply_markup=_main_keyboard())

    # ── История постов ────────────────────────────────────────────────────────
    elif data == "posts_history":
        rows = get_posts_history(20)
        if not rows:
            await q.edit_message_text("История пуста.", reply_markup=_back_keyboard())
            return
        lines = []
        for pid, platform, text, published, sched_at, created_at in rows:
            msk = (created_at + timedelta(hours=3)).strftime("%d.%m %H:%M") if created_at else "?"
            status = "✅" if published else "⏳"
            lines.append(f"{status} #{pid} [{platform}] {msk}\n{text[:80]}…")
        await q.edit_message_text("\n\n".join(lines), reply_markup=_back_keyboard())

    # ── Создать пост ──────────────────────────────────────────────────────────
    elif data == "create_post":
        await q.edit_message_text(
            "Выбери платформу для публикации:",
            reply_markup=_platform_keyboard("post_platform"),
        )

    elif data.startswith("post_platform_"):
        platform = data.replace("post_platform_", "")
        USER_GENERATED[uid] = {"platform": f"post_{platform}"}
        USER_STATE[uid] = "awaiting_post_topic"
        await q.edit_message_text(
            "Опиши тему поста. Например:\n"
            "«Утренний цигун для пробуждения энергии»\n"
            "«Ошибки новичков в практике»\n"
            "«Анонс занятия в субботу»",
            reply_markup=_back_keyboard(),
        )

    # ── Создать Reel ──────────────────────────────────────────────────────────
    elif data == "create_reel":
        await q.edit_message_text(
            "Выбери шаблон Reel:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Мотивация / цитата", callback_data="reel_motivation")],
                [InlineKeyboardButton("🎁 Акция / оффер",      callback_data="reel_promo")],
                [InlineKeyboardButton("📢 Анонс занятия",      callback_data="reel_announcement")],
                [InlineKeyboardButton("◀ Назад",               callback_data="main_menu")],
            ]),
        )

    elif data == "reel_motivation":
        USER_GENERATED[uid] = {"reel_type": "motivation"}
        USER_STATE[uid] = "reel_motivation_quote"
        await q.edit_message_text(
            "Введи цитату или мысль для Reel (1–2 строки):",
            reply_markup=_back_keyboard("create_reel"),
        )

    elif data == "reel_promo":
        USER_GENERATED[uid] = {"reel_type": "promo"}
        USER_STATE[uid] = "reel_promo_data"
        await q.edit_message_text(
            "Введи данные акции через / :\n"
            "<b>Заголовок / Цена / Подтекст / CTA-кнопка</b>\n\n"
            "Пример: Пробное занятие / 500 р. / Первый шаг к здоровью / Записаться",
            parse_mode="HTML",
            reply_markup=_back_keyboard("create_reel"),
        )

    elif data == "reel_announcement":
        USER_GENERATED[uid] = {"reel_type": "announcement"}
        USER_STATE[uid] = "reel_announcement_data"
        await q.edit_message_text(
            "Введи данные анонса через / :\n"
            "<b>Тип / Дата / Тренер / Место</b> [/ Места / Подпись]\n\n"
            "Пример: Цигун Пяти животных / 28 июня / Иван Петров / Онлайн",
            parse_mode="HTML",
            reply_markup=_back_keyboard("create_reel"),
        )

    # ── Видео-фон для Reel ────────────────────────────────────────────────────
    elif data.startswith("reel_bg_"):
        choice = data.replace("reel_bg_", "")
        gen = USER_GENERATED.get(uid, {})
        if choice == "none":
            gen["bg_video"] = None
        elif choice == "gradient":
            gen["bg_video"] = None
        else:
            gen["bg_video"] = str(VIDEOS_DIR / choice)
        USER_GENERATED[uid] = gen
        await _ask_audio(update, ctx, uid, gen)

    # ── Аудио для Reel ────────────────────────────────────────────────────────
    elif data.startswith("reel_audio_"):
        choice = data.replace("reel_audio_", "")
        gen = USER_GENERATED.get(uid, {})
        bg_video = gen.get("bg_video")
        if choice == "none":
            gen["audio_path"] = None
        elif choice == "from_video" and bg_video:
            out = tempfile.mktemp(suffix=".aac")
            if _extract_bg_audio(bg_video, out):
                gen["audio_path"] = out
            else:
                gen["audio_path"] = None
        else:
            gen["audio_path"] = str(AUDIO_DIR / choice)
        USER_GENERATED[uid] = gen
        await _ask_duration(update, ctx, uid)

    # ── Длительность Reel ────────────────────────────────────────────────────
    elif data.startswith("reel_dur_"):
        duration = int(data.replace("reel_dur_", ""))
        gen = USER_GENERATED.get(uid, {})
        gen["duration"] = duration
        USER_GENERATED[uid] = gen
        await _render_and_preview_reel(update, ctx, uid)

    # ── Публикация Reel ──────────────────────────────────────────────────────
    elif data.startswith("reel_pub_"):
        platform = data.replace("reel_pub_", "")
        gen = USER_GENERATED.get(uid, {})
        video_path = gen.get("video_path")
        if not video_path or not Path(video_path).exists():
            await q.edit_message_text("Видео не найдено. Создай Reel заново.",
                                      reply_markup=_main_keyboard())
            return
        sent = False
        if platform in ("tg", "both"):
            try:
                with open(video_path, "rb") as f:
                    await ctx.bot.send_video(chat_id=TG_CHANNEL, video=f,
                                             supports_streaming=True)
                sent = True
            except Exception as e:
                await q.edit_message_text(f"Ошибка Telegram: {e}",
                                          reply_markup=_main_keyboard())
                return
        if platform in ("vk", "both"):
            if not await _vk_clip(video_path):
                await ctx.bot.send_message(uid, "⚠️ VK Клип не удалось опубликовать.")
            else:
                sent = True
        msg = "✅ Reel опубликован!" if sent else "⚠️ Не удалось опубликовать."
        await q.edit_message_text(msg, reply_markup=_main_keyboard())

    # ── Контент-план ─────────────────────────────────────────────────────────
    elif data == "content_plan":
        await q.edit_message_text(
            "Контент-план:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Текущий план",    callback_data="cp_view")],
                [InlineKeyboardButton("✨ Создать новый",   callback_data="cp_generate")],
                [InlineKeyboardButton("◀ Назад",           callback_data="main_menu")],
            ]),
        )

    elif data == "cp_generate":
        month = datetime.now().strftime("%Y-%m")
        USER_GENERATED[uid] = {"cp_month": month}
        USER_STATE[uid] = "awaiting_cp_topic"
        await q.edit_message_text(
            f"Контент-план на {month}.\n\n"
            "Напиши фокус месяца или оставь пустым для авто-генерации.\n"
            "Пример: «Начало лета — энергия и оздоровление»",
            reply_markup=_back_keyboard("content_plan"),
        )

    elif data == "cp_view":
        month = datetime.now().strftime("%Y-%m")
        plan = get_content_plan(month)
        if not plan:
            await q.edit_message_text(
                "Планa на этот месяц нет. Сначала создай его.",
                reply_markup=_back_keyboard("content_plan"),
            )
            return
        plan_id, items_json, approved = plan
        try:
            items = json.loads(items_json)
        except Exception:
            items = []
        lines = [f"{'✅' if approved else '⏳'} Контент-план {month}:\n"]
        for it in items[:15]:
            lines.append(f"День {it.get('day','?')}: {it.get('topic','?')} [{it.get('format','?')}]")
        btns = []
        if not approved:
            btns.append([InlineKeyboardButton("✅ Утвердить", callback_data=f"cp_approve_{plan_id}")])
        btns.append([InlineKeyboardButton("◀ Назад", callback_data="content_plan")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("cp_approve_"):
        plan_id = int(data.replace("cp_approve_", ""))
        approve_content_plan(plan_id)
        await q.edit_message_text("✅ Контент-план утверждён!", reply_markup=_main_keyboard())

    # ── Подписные ссылки ─────────────────────────────────────────────────────
    elif data == "subscriptions":
        await _show_subscriptions(update, ctx, uid)

    elif data == "sub_add":
        USER_STATE[uid] = "awaiting_sub_name"
        USER_GENERATED[uid] = {}
        await q.edit_message_text(
            "Введи название площадки:\n"
            "Пример: Boosty, VK Donut, Patreon, YouTube Members",
            reply_markup=_back_keyboard("subscriptions"),
        )

    elif data.startswith("sub_delete_"):
        link_id = int(data.replace("sub_delete_", ""))
        delete_subscription_link(link_id)
        await _show_subscriptions(update, ctx, uid)

    elif data.startswith("sub_copy_"):
        link_id = int(data.replace("sub_copy_", ""))
        links = get_subscription_links()
        for lid, name, url, desc in links:
            if lid == link_id:
                text = f"🔗 {name}"
                if desc:
                    text += f" — {desc}"
                text += f"\n{url}"
                await ctx.bot.send_message(uid, text)
                await q.answer("Ссылка скопирована в чат")
                return
        await q.answer("Не найдено")

    # ── Медиа ─────────────────────────────────────────────────────────────────
    elif data == "media_menu":
        videos = list(VIDEOS_DIR.glob("*.mp4"))
        audios = list(AUDIO_DIR.glob("*.mp3")) + list(AUDIO_DIR.glob("*.aac"))
        has_logo = LOGO_PATH.exists()
        await q.edit_message_text(
            f"Медиа-библиотека:\n"
            f"🎥 Видео-фоны: {len(videos)}\n"
            f"🎵 Аудио-треки: {len(audios)}\n"
            f"🖼 Логотип: {'есть' if has_logo else 'не загружен'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Загрузить видео-фон",    callback_data="upload_video")],
                [InlineKeyboardButton("📤 Загрузить аудио-трек",   callback_data="upload_audio")],
                [InlineKeyboardButton("📤 Загрузить логотип",      callback_data="upload_logo")],
                [InlineKeyboardButton("🗑 Удалить видео-фон",      callback_data="delete_video_menu")],
                [InlineKeyboardButton("🗑 Удалить аудио-трек",     callback_data="delete_audio_menu")],
                [InlineKeyboardButton("◀ Назад",                   callback_data="main_menu")],
            ]),
        )

    elif data == "upload_video":
        USER_STATE[uid] = "awaiting_bg_video"
        await q.edit_message_text("Отправь .mp4 файл для использования как фон в Reels.",
                                  reply_markup=_back_keyboard("media_menu"))

    elif data == "upload_audio":
        USER_STATE[uid] = "awaiting_bg_audio"
        await q.edit_message_text("Отправь аудио-файл (.mp3 / .m4a / .aac / .ogg).",
                                  reply_markup=_back_keyboard("media_menu"))

    elif data == "upload_logo":
        USER_STATE[uid] = "awaiting_logo"
        await q.edit_message_text("Отправь фото логотипа (PNG или JPEG).",
                                  reply_markup=_back_keyboard("media_menu"))

    elif data == "delete_video_menu":
        rows = get_bg_videos()
        if not rows:
            await q.edit_message_text("Нет загруженных видео-фонов.",
                                      reply_markup=_back_keyboard("media_menu"))
            return
        btns = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_video_{vid_id}")]
                for vid_id, name, _ in rows]
        btns.append([InlineKeyboardButton("◀ Назад", callback_data="media_menu")])
        await q.edit_message_text("Выбери видео для удаления:",
                                  reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("del_video_"):
        vid_id = int(data.replace("del_video_", ""))
        rows = get_bg_videos()
        for row_id, name, _ in rows:
            if row_id == vid_id:
                delete_bg_video(vid_id)
                p = VIDEOS_DIR / name
                if p.exists():
                    p.unlink()
                break
        await q.edit_message_text("✅ Видео удалено.", reply_markup=_back_keyboard("media_menu"))

    elif data == "delete_audio_menu":
        rows = get_bg_audios()
        if not rows:
            await q.edit_message_text("Нет загруженных аудио-треков.",
                                      reply_markup=_back_keyboard("media_menu"))
            return
        btns = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_audio_{aud_id}")]
                for aud_id, name, _ in rows]
        btns.append([InlineKeyboardButton("◀ Назад", callback_data="media_menu")])
        await q.edit_message_text("Выбери аудио для удаления:",
                                  reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("del_audio_"):
        aud_id = int(data.replace("del_audio_", ""))
        rows = get_bg_audios()
        for row_id, name, _ in rows:
            if row_id == aud_id:
                delete_bg_audio(aud_id)
                p = AUDIO_DIR / name
                if p.exists():
                    p.unlink()
                break
        await q.edit_message_text("✅ Аудио удалено.", reply_markup=_back_keyboard("media_menu"))

    # ── Публикация обычного поста ─────────────────────────────────────────────
    elif data.startswith("pub_now_"):
        platform = data.replace("pub_now_", "")
        gen = USER_GENERATED.get(uid, {})
        text = gen.get("post_text", "")
        photo = gen.get("photo_bytes")
        if not text:
            await q.edit_message_text("Текст не найден.", reply_markup=_main_keyboard())
            return
        ok_tg = ok_vk = True
        if platform in ("tg", "both"):
            try:
                if photo:
                    await ctx.bot.send_photo(TG_CHANNEL, photo=photo, caption=text)
                else:
                    await ctx.bot.send_message(TG_CHANNEL, text=text)
            except Exception as e:
                ok_tg = False
                await ctx.bot.send_message(uid, f"Ошибка TG: {e}")
        vk_err = ""
        if platform in ("vk", "both"):
            ok_vk, vk_err = await _vk_post(text, photo)
        db_platform = {"tg": "post_tg", "vk": "post_vk", "both": "post_both"}.get(platform, "post_tg")
        post_id = save_post(db_platform, text, photo, scheduled_at=None)
        mark_post_published(post_id)
        if ok_tg and ok_vk:
            msg = "✅ Опубликовано!"
        elif vk_err:
            msg = f"⚠️ Ошибка VK: {vk_err}"
        else:
            msg = "⚠️ Частично опубликовано."
        await q.edit_message_text(msg, reply_markup=_main_keyboard())

    elif data.startswith("pub_sched_"):
        platform = data.replace("pub_sched_", "")
        USER_GENERATED[uid]["sched_platform"] = platform
        USER_STATE[uid] = "awaiting_schedule_time"
        await q.edit_message_text(
            "Введи дату и время публикации (МСК):\n"
            "Формат: ДД.ММ ЧЧ:ММ\n"
            "Пример: 28.06 10:00",
            reply_markup=_back_keyboard(),
        )

    elif data == "pub_regenerate":
        gen = USER_GENERATED.get(uid, {})
        topic = gen.get("post_topic", "")
        if not topic:
            await q.edit_message_text("Тема не найдена.", reply_markup=_main_keyboard())
            return
        USER_STATE[uid] = "generating_post"
        await q.edit_message_text("Генерирую заново...")
        await _do_generate_post(update, ctx, uid, topic)

    elif data == "pub_platform_choice":
        gen = USER_GENERATED.get(uid, {})
        raw = gen.get("platform", "post_tg").replace("post_", "")
        await q.edit_message_text(
            "Где опубликовать?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Telegram",   callback_data=f"pub_now_tg")],
                [InlineKeyboardButton("ВКонтакте", callback_data=f"pub_now_vk")],
                [InlineKeyboardButton("Обе",        callback_data=f"pub_now_both")],
                [InlineKeyboardButton("📅 По расписанию TG",  callback_data="pub_sched_tg")],
                [InlineKeyboardButton("📅 По расписанию VK",  callback_data="pub_sched_vk")],
                [InlineKeyboardButton("📅 По расписанию Обе", callback_data="pub_sched_both")],
                [InlineKeyboardButton("🔄 Перегенерировать",  callback_data="pub_regenerate")],
                [InlineKeyboardButton("◀ Отмена",             callback_data="main_menu")],
            ]),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Показ подписных ссылок
# ═══════════════════════════════════════════════════════════════════════════════

async def _show_subscriptions(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    links = get_subscription_links()
    q = update.callback_query
    if not links:
        await q.edit_message_text(
            "Подписных ссылок нет. Добавь первую!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить ссылку", callback_data="sub_add")],
                [InlineKeyboardButton("◀ Назад",           callback_data="main_menu")],
            ]),
        )
        return
    lines = ["🔗 Подписные площадки:\n"]
    btns = []
    for lid, name, url, desc in links:
        line = f"{name}"
        if desc:
            line += f" — {desc}"
        lines.append(line)
        btns.append([
            InlineKeyboardButton(f"📤 {name}", callback_data=f"sub_copy_{lid}"),
            InlineKeyboardButton("🗑",          callback_data=f"sub_delete_{lid}"),
        ])
    btns.append([InlineKeyboardButton("➕ Добавить", callback_data="sub_add")])
    btns.append([InlineKeyboardButton("◀ Назад",    callback_data="main_menu")])
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(btns),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Обработка текстовых сообщений (состояния)
# ═══════════════════════════════════════════════════════════════════════════════

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return

    state = USER_STATE.get(uid)

    # ── Тема поста ────────────────────────────────────────────────────────────
    if state == "awaiting_post_topic":
        topic = update.message.text.strip()
        USER_GENERATED[uid]["post_topic"] = topic
        USER_STATE[uid] = "generating_post"
        await update.message.reply_text("Генерирую пост...")
        await _do_generate_post(update, ctx, uid, topic)

    # ── Тема контент-плана ────────────────────────────────────────────────────
    elif state == "awaiting_cp_topic":
        focus = update.message.text.strip()
        month = USER_GENERATED.get(uid, {}).get("cp_month", datetime.now().strftime("%Y-%m"))
        await update.message.reply_text("Создаю контент-план...")
        try:
            prompt = (
                f"Создай контент-план на месяц {month} для Telegram-канала и группы ВКонтакте "
                f"о целительном цигун и ушу. Фокус месяца: {focus or 'общее оздоровление'}. "
                "30 постов. Верни ТОЛЬКО JSON."
            )
            raw = await asyncio.to_thread(_generate_text, prompt, CONTENT_PLAN_SYSTEM_PROMPT)
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            items = json.loads(raw[start:end]) if start >= 0 else []
            save_content_plan(month, json.dumps(items, ensure_ascii=False))
            CONTENT_PLANS[uid] = items
            lines = [f"✅ Контент-план на {month} создан ({len(items)} постов):\n"]
            for it in items[:10]:
                lines.append(f"День {it.get('day','?')}: {it.get('topic','?')}")
            lines.append("...")
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Утвердить", callback_data=f"cp_approve_new")],
                    [InlineKeyboardButton("◀ Меню",      callback_data="main_menu")],
                ]),
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}", reply_markup=_back_keyboard())
        finally:
            USER_STATE.pop(uid, None)

    # ── Добавление подписки — название ────────────────────────────────────────
    elif state == "awaiting_sub_name":
        USER_GENERATED[uid]["sub_name"] = update.message.text.strip()
        USER_STATE[uid] = "awaiting_sub_url"
        await update.message.reply_text("Введи ссылку (URL):")

    elif state == "awaiting_sub_url":
        USER_GENERATED[uid]["sub_url"] = update.message.text.strip()
        USER_STATE[uid] = "awaiting_sub_desc"
        await update.message.reply_text("Введи короткое описание (или отправь «-» чтобы пропустить):")

    elif state == "awaiting_sub_desc":
        desc = update.message.text.strip()
        if desc == "-":
            desc = ""
        gen = USER_GENERATED.get(uid, {})
        link_id = add_subscription_link(gen.get("sub_name",""), gen.get("sub_url",""), desc)
        USER_STATE.pop(uid, None)
        USER_GENERATED.pop(uid, None)
        await update.message.reply_text(
            f"✅ Ссылка добавлена (ID {link_id}).",
            reply_markup=_main_keyboard(),
        )

    # ── Время отложенного поста ───────────────────────────────────────────────
    elif state == "awaiting_schedule_time":
        raw = update.message.text.strip()
        try:
            year = datetime.now().year
            dt_msk = datetime.strptime(f"{year} {raw}", "%Y %d.%m %H:%M")
            dt_utc = dt_msk - timedelta(hours=3)
            gen = USER_GENERATED.get(uid, {})
            platform = gen.get("sched_platform", "tg")
            db_platform = {"tg": "post_tg", "vk": "post_vk", "both": "post_both"}.get(platform, "post_tg")
            post_id = save_post(db_platform, gen.get("post_text",""),
                                gen.get("photo_bytes"), scheduled_at=dt_utc)
            USER_STATE.pop(uid, None)
            msk_str = dt_msk.strftime("%d.%m.%Y %H:%M")
            await update.message.reply_text(
                f"✅ Пост #{post_id} запланирован на {msk_str} МСК.",
                reply_markup=_main_keyboard(),
            )
        except ValueError:
            await update.message.reply_text(
                "Неверный формат. Введи: ДД.ММ ЧЧ:ММ\nПример: 28.06 10:00"
            )

    # ── Reel: цитата (мотивация) ──────────────────────────────────────────────
    elif state == "reel_motivation_quote":
        USER_GENERATED[uid]["quote"] = update.message.text.strip()
        USER_STATE[uid] = "reel_motivation_sub"
        await update.message.reply_text("Введи подпись (название канала или CTA):")

    elif state == "reel_motivation_sub":
        USER_GENERATED[uid]["subtext"] = update.message.text.strip()
        USER_STATE[uid] = None
        await _ask_bg_video(update, ctx, uid)

    # ── Reel: промо-данные ────────────────────────────────────────────────────
    elif state == "reel_promo_data":
        parts = [p.strip() for p in update.message.text.split("/")]
        if len(parts) < 4:
            await update.message.reply_text(
                "Нужно 4 части через /: Заголовок / Цена / Подтекст / CTA"
            )
            return
        gen = USER_GENERATED.get(uid, {})
        gen.update({"headline": parts[0], "price": parts[1],
                    "subtext": parts[2], "cta": parts[3]})
        USER_GENERATED[uid] = gen
        USER_STATE[uid] = None
        await _ask_bg_video(update, ctx, uid)

    # ── Reel: данные анонса ───────────────────────────────────────────────────
    elif state == "reel_announcement_data":
        parts = [p.strip() for p in update.message.text.split("/")]
        if len(parts) < 4:
            await update.message.reply_text(
                "Нужно минимум 4 части: Тип / Дата / Тренер / Место"
            )
            return
        gen = USER_GENERATED.get(uid, {})
        gen.update({
            "class_type": parts[0], "date": parts[1],
            "trainer":    parts[2], "time": parts[3],
            "spots":      parts[4] if len(parts) > 4 else "",
            "tagline":    parts[5] if len(parts) > 5 else "",
        })
        USER_GENERATED[uid] = gen
        USER_STATE[uid] = None
        await _ask_bg_video(update, ctx, uid)

    # ── Загрузка видео-фона ───────────────────────────────────────────────────
    elif state == "awaiting_bg_video":
        if not (update.message.document or update.message.video):
            await update.message.reply_text("Отправь .mp4 файл.")
            return
        doc = update.message.document or update.message.video
        fname = getattr(doc, "file_name", None) or "bg_video.mp4"
        if not fname.lower().endswith(".mp4"):
            fname = "bg_video.mp4"
        file = await doc.get_file()
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data = buf.getvalue()
        save_bg_video(fname, data)
        p = VIDEOS_DIR / fname
        p.write_bytes(data)
        USER_STATE.pop(uid, None)
        await update.message.reply_text(
            f"✅ Видео-фон «{fname}» загружен.", reply_markup=_main_keyboard()
        )

    # ── Загрузка аудио ────────────────────────────────────────────────────────
    elif state == "awaiting_bg_audio":
        doc = update.message.document or update.message.audio
        if not doc:
            await update.message.reply_text("Отправь аудио-файл.")
            return
        fname = getattr(doc, "file_name", None) or "audio.mp3"
        file = await doc.get_file()
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data = buf.getvalue()
        save_bg_audio(fname, data)
        p = AUDIO_DIR / fname
        p.write_bytes(data)
        USER_STATE.pop(uid, None)
        await update.message.reply_text(
            f"✅ Аудио «{fname}» загружен.", reply_markup=_main_keyboard()
        )

    # ── Загрузка логотипа ─────────────────────────────────────────────────────
    elif state == "awaiting_logo":
        photo = update.message.photo
        doc   = update.message.document
        if photo:
            file = await photo[-1].get_file()
        elif doc:
            file = await doc.get_file()
        else:
            await update.message.reply_text("Отправь фото.")
            return
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data = buf.getvalue()
        save_logo(data)
        LOGO_PATH.write_bytes(data)
        clear_logo_cache()
        USER_STATE.pop(uid, None)
        await update.message.reply_text(
            "✅ Логотип обновлён.", reply_markup=_main_keyboard()
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Генерация поста
# ═══════════════════════════════════════════════════════════════════════════════

async def _do_generate_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            uid: int, topic: str):
    try:
        text = await asyncio.to_thread(
            _generate_text,
            f"Напиши пост для канала о цигун и ушу на тему: {topic}",
        )
        photo = await _get_photo("qigong tai chi meditation")
        USER_GENERATED[uid]["post_text"] = text
        USER_GENERATED[uid]["photo_bytes"] = photo
        USER_STATE.pop(uid, None)

        preview = text[:1000] + ("..." if len(text) > 1000 else "")
        if photo:
            await ctx.bot.send_photo(
                uid, photo=photo, caption=preview,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Опубликовать / расписание",
                                         callback_data="pub_platform_choice")],
                    [InlineKeyboardButton("◀ Отмена", callback_data="main_menu")],
                ]),
            )
        else:
            await ctx.bot.send_message(
                uid, preview,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Опубликовать / расписание",
                                         callback_data="pub_platform_choice")],
                    [InlineKeyboardButton("◀ Отмена", callback_data="main_menu")],
                ]),
            )
    except Exception as e:
        await ctx.bot.send_message(uid, f"Ошибка генерации: {e}",
                                   reply_markup=_main_keyboard())
        USER_STATE.pop(uid, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Reel: выбор фона / аудио / длительности / рендер
# ═══════════════════════════════════════════════════════════════════════════════

async def _ask_bg_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    videos = list(VIDEOS_DIR.glob("*.mp4"))
    btns = [
        [InlineKeyboardButton("🎨 Градиент (без видео)", callback_data="reel_bg_gradient")],
    ]
    for v in videos[:8]:
        btns.append([InlineKeyboardButton(f"🎥 {v.name}", callback_data=f"reel_bg_{v.name}")])
    btns.append([InlineKeyboardButton("◀ Назад", callback_data="create_reel")])
    text = "Выбери видео-фон для Reel:"
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(btns))


async def _ask_audio(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int, gen: dict):
    audios = list(AUDIO_DIR.glob("*.mp3")) + list(AUDIO_DIR.glob("*.aac"))
    btns = [
        [InlineKeyboardButton("🔇 Без аудио",            callback_data="reel_audio_none")],
    ]
    if gen.get("bg_video"):
        btns.append([InlineKeyboardButton("🎵 Аудио из видео-фона",
                                          callback_data="reel_audio_from_video")])
    for a in audios[:8]:
        btns.append([InlineKeyboardButton(f"🎵 {a.name}", callback_data=f"reel_audio_{a.name}")])
    q = update.callback_query
    await q.edit_message_text("Выбери аудио:", reply_markup=InlineKeyboardMarkup(btns))


async def _ask_duration(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    q = update.callback_query
    await q.edit_message_text(
        "Выбери длительность Reel:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("15 сек", callback_data="reel_dur_15")],
            [InlineKeyboardButton("30 сек", callback_data="reel_dur_30")],
            [InlineKeyboardButton("60 сек", callback_data="reel_dur_60")],
        ]),
    )


async def _render_and_preview_reel(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    q = update.callback_query
    gen = USER_GENERATED.get(uid, {})
    reel_type  = gen.get("reel_type")
    bg_video   = gen.get("bg_video")
    audio_path = gen.get("audio_path")
    duration   = gen.get("duration", 15)

    await q.edit_message_text("Рендерю Reel, подожди...")
    try:
        out = tempfile.mktemp(suffix=".mp4")
        if reel_type == "motivation":
            fn = lambda: render_motivation(
                gen["quote"], gen["subtext"], out,
                bg_video=bg_video, audio_path=audio_path, duration_secs=duration,
            )
        elif reel_type == "promo":
            fn = lambda: render_promo(
                gen["headline"], gen["price"], gen["subtext"], gen["cta"], out,
                bg_video=bg_video, audio_path=audio_path, duration_secs=duration,
            )
        else:
            fn = lambda: render_announcement(
                gen["class_type"], gen["trainer"], gen["date"], gen.get("time",""),
                gen.get("spots",""), gen.get("tagline",""), out,
                bg_video=bg_video, audio_path=audio_path, duration_secs=duration,
            )
        render_task = asyncio.create_task(asyncio.to_thread(fn))
        timeout = max(180, duration * 12)
        try:
            await asyncio.wait_for(render_task, timeout=timeout)
        except asyncio.TimeoutError:
            render_task.cancel()
            await q.edit_message_text("Превышен таймаут рендера. Попробуй покороче.",
                                      reply_markup=_main_keyboard())
            return

        gen["video_path"] = out
        USER_GENERATED[uid] = gen

        with open(out, "rb") as f:
            await ctx.bot.send_video(
                uid, video=f, supports_streaming=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Telegram",      callback_data="reel_pub_tg")],
                    [InlineKeyboardButton("📤 ВКонтакте",    callback_data="reel_pub_vk")],
                    [InlineKeyboardButton("📤 Обе",           callback_data="reel_pub_both")],
                    [InlineKeyboardButton("◀ Отмена",         callback_data="main_menu")],
                ]),
                caption="Reel готов. Куда публикуем?"
            )
    except Exception as e:
        logger.error(f"Reel render error: {e}", exc_info=True)
        await ctx.bot.send_message(uid, f"Ошибка рендера: {e}",
                                   reply_markup=_main_keyboard())


# ═══════════════════════════════════════════════════════════════════════════════
# Запуск
# ═══════════════════════════════════════════════════════════════════════════════

async def post_init(app: Application):
    init_db()
    asyncio.create_task(_restore_media())
    asyncio.create_task(_scheduled_post_loop(app.bot))
    logger.info("admin_bot запущен")


def main():
    if not ADMIN_BOT_TOKEN:
        raise ValueError("ADMIN_BOT_TOKEN не задан")
    app = (
        Application.builder()
        .token(ADMIN_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio as _asyncio
    _asyncio.set_event_loop(_asyncio.new_event_loop())
    main()
