"""
Бот-администратор для канала «Оздоровительный цигун и ушу».
Функции: генерация постов, Reels, контент-план, ссылки на подписки, медиа.
"""
import asyncio
import io
from io import BytesIO
import json
import logging
import os
import random
import re
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
    delete_subscription_link, save_bg_video, delete_bg_video,
    get_bg_videos_meta, get_bg_video_data, get_bg_audios_meta, get_bg_audio_data,
    save_bg_audio, delete_bg_audio, save_logo, get_logo,
    save_post_photo, get_post_photos, delete_post_photo,
    save_youtube_video, mark_youtube_published, get_youtube_videos,
    update_youtube_video_script, update_youtube_video_description,
    update_youtube_video_title, get_youtube_video, get_youtube_video_by_ytid,
    get_growth_tasks, toggle_growth_task, get_next_pending_task,
    get_last_growth_reminder_date, set_last_growth_reminder_date,
    get_last_yt_digest_date, set_last_yt_digest_date,
    get_recent_chat_histories, load_chat_history,
)
import youtube_api as _yt
from agents.youtube_agent import youtube_master as _yt_master
from data.knowledge import CONTENT_PLAN_SYSTEM_PROMPT, POST_SYSTEM_PROMPT
from py_render import (
    clear_logo_cache, render_announcement, render_motivation, render_promo,
    _extract_bg_audio, concat_clips,
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

def _generate_text(user_prompt: str, system: str | None = None, max_tokens: int = 2000) -> str:
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
                max_tokens=max_tokens,
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
                model=model, messages=messages, max_tokens=max_tokens,
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
            if photo_bytes and not attachments:
                warn = (
                    "Фото не прикрепилось в VK (пост ушёл без фото) — "
                    "похоже, истёк или не задан VK_USER_TOKEN. Обнови его в Railway."
                )
                logger.warning(f"VK post {warn}")
                return True, warn
            return True, ""
    except Exception as e:
        logger.error(f"VK post error: {e}")
        return False, str(e)


async def _vk_clip(video_path: str) -> tuple[bool, str]:
    token = VK_USER_TOKEN or VK_TOKEN
    if not token:
        return False, "VK_USER_TOKEN не задан"
    if not VK_GROUP_ID:
        return False, "VK_GROUP_ID не задан"
    try:
        group_id = VK_GROUP_ID.lstrip("-")  # API требует положительный ID
        async with httpx.AsyncClient(timeout=120) as cl:
            r = await cl.get(
                "https://api.vk.com/method/video.save",
                params={
                    "group_id": group_id, "name": "Reel",
                    "is_private": 0, "wallpost": 1,
                    "access_token": token, "v": "5.131",
                },
            )
            rj = r.json()
            if "error" in rj:
                err = rj["error"]
                msg = f"video.save: [{err.get('error_code')}] {err.get('error_msg')}"
                logger.error(f"VK clip error: {msg}")
                return False, msg
            upload_url = rj["response"]["upload_url"]
            with open(video_path, "rb") as f:
                up = await cl.post(upload_url, files={"video_file": f}, timeout=120)
            logger.info(f"VK clip upload response: {up.text[:300]}")
        return True, ""
    except Exception as e:
        logger.error(f"VK clip exception: {e}")
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# Скачивание видео по ссылке (обход лимита Telegram Bot API в 20 МБ на файл)
# ═══════════════════════════════════════════════════════════════════════════════

def _download_video_from_url_sync(url: str, dest: Path):
    """Скачивает видео по прямой ссылке или публичной ссылке Яндекс.Диска в dest.

    Сама загрузка идёт через requests, а не httpx: у Яндекса стоит анти-бот
    защита (похоже, по TLS/HTTP-отпечатку клиента) — httpx стабильно получает
    403 Forbidden на download-ссылке, хотя та же самая ссылка через requests
    (и через curl) отдаётся без проблем."""
    import requests
    download_url = url
    if "disk.yandex" in url:
        r = requests.get(
            "https://cloud-api.yandex.net/v1/disk/public/resources/download",
            params={"public_key": url}, timeout=30,
        )
        r.raise_for_status()
        download_url = r.json()["href"]

    with requests.get(download_url, stream=True, timeout=280) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


async def _download_video_from_url(url: str, dest: Path):
    await asyncio.to_thread(_download_video_from_url_sync, url, dest)


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
                       photo_bytes: bytes | None) -> tuple[bool, str]:
    ok = True
    warn = ""
    if platform in ("post_tg", "post_both"):
        try:
            await _publish_post_to_tg(bot, text, photo_bytes)
        except Exception as e:
            logger.error(f"TG publish error post {post_id}: {e}")
            ok = False
    if platform in ("post_vk", "post_both"):
        vk_ok, vk_err = await _vk_post(text, photo_bytes)
        if not vk_ok:
            ok = False
        elif vk_err:
            warn = vk_err
    return ok, warn


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
            ok, warn = await _publish_any(bot, post_id, platform, text, pb)
            if ok:
                mark_post_published(post_id)
                _sched_failures.pop(post_id, None)
                msg = f"✅ Пост #{post_id} опубликован автоматически."
                if warn:
                    msg += f"\n⚠️ {warn}"
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, msg)
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


async def _growth_reminder_loop(bot: Bot) -> None:
    """Раз в день (после 10:00 по Москве) напоминает о следующей невыполненной задаче
    из плана роста, если сегодня напоминание ещё не отправлялось."""
    await asyncio.sleep(15)
    logger.info("_growth_reminder_loop: запущен")
    while True:
        try:
            msk_now = datetime.utcnow() + timedelta(hours=3)
            today_str = msk_now.strftime("%Y-%m-%d")
            last_date = await asyncio.to_thread(get_last_growth_reminder_date)
            if last_date != today_str and msk_now.hour >= 10:
                task = await asyncio.to_thread(get_next_pending_task)
                if task:
                    task_id, section, task_text = task
                    section_name = _GROWTH_SECTION_NAMES.get(section, section)
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Сделано", callback_data=f"gt_toggle_{task_id}_{section}")],
                        [InlineKeyboardButton("📋 Весь план", callback_data="growth_menu")],
                    ])
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"🚀 Напоминание по плану роста\n\n{section_name}\n{task_text}",
                                reply_markup=kb,
                            )
                        except Exception as e:
                            logger.warning(f"Не удалось отправить напоминание {admin_id}: {e}")
                await asyncio.to_thread(set_last_growth_reminder_date, today_str)
        except asyncio.CancelledError:
            logger.info("_growth_reminder_loop: завершение (CancelledError)")
            return
        except Exception as e:
            logger.error(f"_growth_reminder_loop error: {e}", exc_info=True)
        try:
            await asyncio.sleep(1800)
        except asyncio.CancelledError:
            logger.info("_growth_reminder_loop: sleep прерван, завершение")
            return


async def _yt_digest_loop(bot: Bot) -> None:
    """По понедельникам после 10:00 по Москве присылает отчёт аналитики YouTube-канала
    (тот же паттерн день-однократной отправки, что _growth_reminder_loop — иначе без
    напоминания об этом легко забыть, что кнопка «📊 Аналитика канала» вообще есть)."""
    await asyncio.sleep(25)
    logger.info("_yt_digest_loop: запущен")
    while True:
        try:
            msk_now = datetime.utcnow() + timedelta(hours=3)
            today_str = msk_now.strftime("%Y-%m-%d")
            if msk_now.weekday() == 0 and msk_now.hour >= 10:
                last_date = await asyncio.to_thread(get_last_yt_digest_date)
                if last_date != today_str:
                    if _yt.is_authorized():
                        report = await asyncio.to_thread(_yt_master.get_analytics_report)
                        for admin_id in ADMIN_IDS:
                            try:
                                await bot.send_message(
                                    admin_id, f"📊 Еженедельная аналитика YouTube\n\n{report}"
                                )
                            except Exception as e:
                                logger.warning(f"Не удалось отправить YouTube-дайджест {admin_id}: {e}")
                    await asyncio.to_thread(set_last_yt_digest_date, today_str)
        except asyncio.CancelledError:
            logger.info("_yt_digest_loop: завершение (CancelledError)")
            return
        except Exception as e:
            logger.error(f"_yt_digest_loop error: {e}", exc_info=True)
        try:
            await asyncio.sleep(1800)
        except asyncio.CancelledError:
            logger.info("_yt_digest_loop: sleep прерван, завершение")
            return


# ═══════════════════════════════════════════════════════════════════════════════
# Восстановление медиа после рестарта Railway
# ═══════════════════════════════════════════════════════════════════════════════

async def _restore_media():
    # Тянем только id+name (без BYTEA data), и только для реально отсутствующих
    # на диске файлов скачиваем их содержимое — раньше скачивались ВСЕ видео/аудио
    # целиком при каждом рестарте Railway, что и сожгло месячную квоту Neon за 4 дня.
    try:
        for vid_id, name in get_bg_videos_meta():
            p = VIDEOS_DIR / name
            if not p.exists():
                data = get_bg_video_data(vid_id)
                if data:
                    p.write_bytes(bytes(data))
        logger.info("Видео-фоны восстановлены")
    except Exception as e:
        logger.warning(f"Ошибка восстановления видео: {e}")

    try:
        for aud_id, name in get_bg_audios_meta():
            p = AUDIO_DIR / name
            if not p.exists():
                data = get_bg_audio_data(aud_id)
                if data:
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

    try:
        for photo_id, name, data in get_post_photos():
            p = PHOTOS_DIR / name
            if not p.exists():
                p.write_bytes(bytes(data))
        logger.info("Фото для постов восстановлены")
    except Exception as e:
        logger.warning(f"Ошибка восстановления фото: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Вспомогательные клавиатуры
# ═══════════════════════════════════════════════════════════════════════════════

def _main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Создать пост",      callback_data="create_post")],
        [InlineKeyboardButton("🎬 Создать Reel",       callback_data="create_reel")],
        [InlineKeyboardButton("▶️ YouTube",            callback_data="youtube_menu")],
        [InlineKeyboardButton("📅 Контент-план",       callback_data="content_plan")],
        [InlineKeyboardButton("🔗 Подписные ссылки",   callback_data="subscriptions")],
        [InlineKeyboardButton("🎥 Медиа",              callback_data="media_menu")],
        [InlineKeyboardButton("📋 История постов",     callback_data="posts_history")],
        [InlineKeyboardButton("🚀 План роста",         callback_data="growth_menu")],
        [InlineKeyboardButton("💬 Диалоги клиентов",   callback_data="dialogs_menu")],
    ])


def _youtube_keyboard():
    auth_status = "✅ Авторизован" if _yt.is_authorized() else "🔑 Авторизоваться"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(auth_status,              callback_data="yt_auth")],
        [InlineKeyboardButton("✍️ Подготовить Shorts",  callback_data="yt_prepare_shorts")],
        [InlineKeyboardButton("🎬 Подготовить видео",   callback_data="yt_prepare_video")],
        [InlineKeyboardButton("📊 Аналитика канала",    callback_data="yt_analytics")],
        [InlineKeyboardButton("📋 История видео",       callback_data="yt_history")],
        [InlineKeyboardButton("🔗 Правка видео по ссылке", callback_data="yt_editpub_byurl")],
        [InlineKeyboardButton("📃 Собрать плейлист «Бадуаньцзин»", callback_data="yt_playlist_backfill")],
        [InlineKeyboardButton("◀ Главное меню",         callback_data="main_menu")],
    ])


_BADUANJIN_PLAYLIST_TITLE = "Бадуаньцзин за 21 день"
_BADUANJIN_PLAYLIST_DESC = (
    "21-дневная бесплатная программа Бадуаньцзин — 8 упражнений с прогрессией по дням.\n\n"
    "📌 Telegram-канал: https://t.me/baguazhangspb\n"
    "💛 Бесплатно на Boosty: https://boosty.to/s.borulko"
)
# Видео из первого забэкфилла плейлиста, в смысловом порядке: тизер-обзор →
# упражнение 1 → упражнение 2 → бонусная техника. Новые видео серии добавляются
# по одному через кнопку «➕ В плейлист «Бадуаньцзин»» на карточке видео.
_BADUANJIN_BACKFILL_IDS = ["9YCSSXnZaRA", "pVAik2Riqv8", "ens-J5aJeAo", "UHyi6M7HQuI"]


async def _get_or_create_baduanjin_playlist() -> str:
    playlists = await asyncio.to_thread(_yt.list_playlists)
    for pl in playlists:
        if pl["title"] == _BADUANJIN_PLAYLIST_TITLE:
            return pl["id"]
    return await asyncio.to_thread(
        _yt.create_playlist, _BADUANJIN_PLAYLIST_TITLE, _BADUANJIN_PLAYLIST_DESC, "public"
    )


_YT_ID_RE = re.compile(r'(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/))([A-Za-z0-9_-]{11})')


def _extract_youtube_id(text: str) -> str | None:
    """Достаёт video ID из ссылки youtu.be/watch/shorts или принимает голый ID (11 символов)."""
    text = text.strip()
    m = _YT_ID_RE.search(text)
    if m:
        return m.group(1)
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', text):
        return text
    return None


async def _render_editpub_detail(uid: int, yt_id: str, video_db_id: int | None):
    """Общий рендер экрана правки для видео — не важно, найдено оно через
    «История видео» (video_db_id известен) или введено по ссылке (video_db_id=None,
    видео может быть не отслежено ботом вообще, например загружено вручную)."""
    live = await asyncio.to_thread(_yt.get_video_snippet, yt_id)
    USER_GENERATED.setdefault(uid, {})["yt_editpub_id"] = video_db_id
    USER_GENERATED[uid]["yt_editpub_ytid"] = yt_id
    text = (
        f"✏️ Правка видео https://youtu.be/{yt_id}\n\n"
        f"📌 Название сейчас:\n{live.get('title', '')}\n\n"
        f"📝 Описание сейчас (первые 300 симв.):\n{live.get('description', '')[:300]}..."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить название", callback_data="yt_editpub_title")],
        [InlineKeyboardButton("✏️ Изменить описание", callback_data="yt_editpub_desc")],
        [InlineKeyboardButton("➕ В плейлист «Бадуаньцзин»", callback_data="yt_addplaylist")],
        [InlineKeyboardButton("◀ Назад", callback_data="youtube_menu")],
    ])
    return text, kb


def _platform_keyboard(prefix: str = "platform"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Telegram",    callback_data=f"{prefix}_tg")],
        [InlineKeyboardButton("ВКонтакте",  callback_data=f"{prefix}_vk")],
        [InlineKeyboardButton("Обе",        callback_data=f"{prefix}_both")],
        [InlineKeyboardButton("◀ Назад",    callback_data="main_menu")],
    ])

def _back_keyboard(cb: str = "main_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Назад", callback_data=cb)]])


_GROWTH_SECTION_NAMES = {
    "launch": "📅 План запуска",
    "playbook": "📣 Плейбук клиентов",
    "revenue": "💰 Рост дохода",
}


async def _render_growth_menu():
    """Верхний уровень — только разделы, с прогрессом по каждому. Заходишь внутрь раздела,
    чтобы увидеть и отметить конкретные задачи."""
    tasks = await asyncio.to_thread(get_growth_tasks)
    by_section: dict[str, list] = {}
    for tid, section, task_text, done in tasks:
        by_section.setdefault(section, []).append((tid, task_text, done))
    total = len(tasks)
    done_total = sum(1 for _, _, _, done in tasks if done)
    text = f"🚀 План роста «Бадуаньцзин за 21 день»\n\nВыполнено {done_total} из {total}. Выбери раздел:"
    btns = []
    for section, name in _GROWTH_SECTION_NAMES.items():
        items = by_section.get(section, [])
        done_n = sum(1 for _, _, done in items if done)
        btns.append([InlineKeyboardButton(f"{name} ({done_n}/{len(items)})", callback_data=f"gt_section_{section}")])
    btns.append([InlineKeyboardButton("◀ Назад", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(btns)


async def _render_growth_section(section: str):
    """Детальный вид одного раздела — задачи с чекбоксами. Для launch дополнительно
    показывает заголовки недель, извлечённые из текста задачи («Неделя N · ...»)."""
    tasks = await asyncio.to_thread(get_growth_tasks)
    items = [(tid, task_text, done) for tid, sec, task_text, done in tasks if sec == section]
    name = _GROWTH_SECTION_NAMES.get(section, section)
    done_n = sum(1 for _, _, done in items if done)
    text = f"{name}\n\nВыполнено {done_n} из {len(items)}. Нажми на задачу, чтобы отметить."
    btns = []
    current_week = None
    for tid, task_text, done in items:
        week, sep, rest = task_text.partition(" · ")
        label_text = rest if sep else task_text
        if sep and week != current_week:
            current_week = week
            btns.append([InlineKeyboardButton(f"— {week} —", callback_data=f"gt_section_{section}")])
        mark = "✅" if done else "⬜"
        btns.append([InlineKeyboardButton(f"{mark} {label_text[:55]}", callback_data=f"gt_toggle_{tid}_{section}")])
    btns.append([InlineKeyboardButton("◀ К разделам", callback_data="growth_menu")])
    return text, InlineKeyboardMarkup(btns)


async def _render_dialogs_menu(uid: int):
    """Список последних диалогов Конфуция с клиентами (Telegram + VK), т.к. в
    отличие от VK у Telegram нет встроенного способа посмотреть переписку бота."""
    rows = await asyncio.to_thread(get_recent_chat_histories, 15)
    if not rows:
        return "Диалогов пока нет.", _back_keyboard()
    gen = USER_GENERATED.setdefault(uid, {})
    dlg_list = []
    btns = []
    for row_user_id, source, history, updated_at in rows:
        dlg_list.append((row_user_id, source))
        idx = len(dlg_list) - 1
        platform = "TG" if source == "telegram" else "VK"
        updated_msk = (updated_at + timedelta(hours=3)).strftime("%d.%m %H:%M") if updated_at else "?"
        last_msg = ""
        for msg in reversed(history or []):
            if msg.get("role") == "user":
                last_msg = msg.get("content", "").strip()[:35]
                break
        label = f"[{platform}] {row_user_id} · {updated_msk} · {last_msg}"
        btns.append([InlineKeyboardButton(label[:64], callback_data=f"dlg_view_{idx}")])
    gen["dlg_list"] = dlg_list
    USER_GENERATED[uid] = gen
    btns.append([InlineKeyboardButton("◀ Назад", callback_data="main_menu")])
    return "💬 Последние диалоги клиентов с Конфуцием — выбери, чтобы посмотреть переписку:", InlineKeyboardMarkup(btns)


async def _safe_edit(q, text: str, reply_markup=None, **kwargs):
    """edit_message_text падает на фото-сообщениях — тогда отправляем новым сообщением."""
    try:
        await q.edit_message_text(text, reply_markup=reply_markup, **kwargs)
    except Exception:
        await q.message.reply_text(text, reply_markup=reply_markup, **kwargs)


async def _preview_post(bot, uid: int, photo: bytes | None, text: str, kb):
    """Показывает превью поста администратору. Полный текст без обрезания."""
    if photo and len(text) <= 1024:
        await bot.send_photo(uid, photo=photo, caption=text, reply_markup=kb)
    elif photo:
        await bot.send_photo(uid, photo=photo)
        await bot.send_message(uid, text, reply_markup=kb)
    else:
        await bot.send_message(uid, text, reply_markup=kb)


async def _reply_preview(message, photo: bytes | None, text: str, kb):
    """reply_*-версия _preview_post: для ответа в on_message-обработчиках."""
    if photo and len(text) <= 1024:
        await message.reply_photo(photo=io.BytesIO(photo), caption=text, reply_markup=kb)
    elif photo:
        await message.reply_photo(photo=io.BytesIO(photo))
        await message.reply_text(text, reply_markup=kb)
    else:
        await message.reply_text(text, reply_markup=kb)


async def _publish_post_to_tg(bot, text: str, photo: bytes | None):
    """Публикует пост в TG-канал. Фото и текст всегда в одном сообщении."""
    import html as _html
    if not photo:
        await bot.send_message(TG_CHANNEL, text)
        return
    if len(text) <= 1024:
        await bot.send_photo(TG_CHANNEL, photo=photo, caption=text)
        return
    # Текст > 1024 симв.: невидимая ссылка на фото + полный текст одним сообщением
    try:
        # Загружаем фото через личку первого админа чтобы получить прямой URL
        if ADMIN_IDS:
            tmp = await bot.send_photo(ADMIN_IDS[0], photo=photo)
            f = await bot.get_file(tmp.photo[-1].file_id)
            url = f"https://api.telegram.org/file/bot{ADMIN_BOT_TOKEN}/{f.file_path}"
            await tmp.delete()
            await bot.send_message(
                TG_CHANNEL,
                f'<a href="{url}">&#8203;</a>{_html.escape(text)}',
                parse_mode="HTML",
            )
        else:
            raise ValueError("нет ADMIN_IDS")
    except Exception:
        # Fallback: всё же отправляем раздельно
        await bot.send_photo(TG_CHANNEL, photo=photo)
        await bot.send_message(TG_CHANNEL, text)


def _publish_keyboard(platform: str):
    """Кнопки публикации после генерации поста."""
    label = {"tg": "Telegram", "vk": "ВКонтакте", "both": "обе платформы"}.get(platform, platform)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Опубликовать ({label})", callback_data=f"pub_now_{platform}")],
        [InlineKeyboardButton("⏰ По расписанию",           callback_data=f"pub_sched_{platform}")],
        [InlineKeyboardButton("📷 Поменять фото",           callback_data="pub_change_photo")],
        [InlineKeyboardButton("✏️ Редактировать текст",    callback_data="pub_edit_text")],
        [InlineKeyboardButton("🔄 Переделать",              callback_data="pub_regenerate")],
        [InlineKeyboardButton("◀ Главное меню",            callback_data="main_menu")],
    ])


_FORMAT_ICON = {
    "анонс":         "📢",
    "совет":         "💡",
    "мотивация":     "🔥",
    "история":       "📖",
    "провокация":    "⚡",
    "польза":        "🌿",
    "мини-практика": "🎯",
    "сравнение":     "⚖️",
}

_MONTHS_RU_GEN = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def _format_plan_text(items: list, month: str) -> str:
    import html as _html_mod
    try:
        yr, mn = month.split("-")
        month_name = f"{_MONTHS_RU_GEN[int(mn)-1]} {yr}"
    except Exception:
        month_name = month
    lines = [f"📅 <b>Контент-план на {month_name}</b>\n"]
    for i, item in enumerate(items, 1):
        day = item.get("day", "?")
        topic = item.get("topic", "")
        fmt = item.get("format", "")
        platform = item.get("platform", "both")
        icon = _FORMAT_ICON.get(fmt.lower(), "📝")
        plat_label = "ВК+TG" if platform == "both" else ("ВК" if platform == "vk" else "TG")
        safe_topic = _html_mod.escape(topic)
        lines.append(f"{i}. {icon} <b>День {day}</b> — {fmt} [{plat_label}]\n<i>{safe_topic}</i>\n")
    return "\n".join(lines)


def _plan_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Утвердить план", callback_data=f"cp_approve_{plan_id}")],
        [InlineKeyboardButton("🔄 Переделать",    callback_data="cp_regen")],
        [InlineKeyboardButton("◀ Назад",          callback_data="content_plan")],
    ])


async def _show_plan_items(q, uid: int, plan_id: int, items: list):
    """Показывает пункты контент-плана с кнопками для генерации."""
    buttons = []
    for i, item in enumerate(items):
        icon = _FORMAT_ICON.get(item.get("format", "").lower(), "📝")
        day = item.get("day", "")
        topic = item.get("topic", "")[:40]
        label = f"{icon} День {day} — {topic}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"plan_item_{plan_id}_{i}")])
    buttons.append([InlineKeyboardButton("🔄 Создать новый план", callback_data="cp_generate")])
    buttons.append([InlineKeyboardButton("◀ Главное меню",        callback_data="main_menu")])
    await q.edit_message_text(
        "📅 Контент-план утверждён. Выбери пост для генерации:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _generate_plan_post(q, uid: int, item: dict, platform: str):
    """Генерирует пост по пункту контент-плана и показывает превью."""
    topic = item.get("topic", "")
    fmt = item.get("format", "пост")
    day = item.get("day", "")
    plat_label = {"tg": "Telegram", "vk": "ВКонтакте", "both": "обе платформы"}.get(platform, platform)

    _fmt_hints = {
        "анонс":         "анонс события или занятия — что будет, когда, почему стоит прийти",
        "совет":         "конкретный совет — механизм + польза + что делать прямо сейчас",
        "мотивация":     "мотивирующий пост от первого лица — маленькая история или инсайт",
        "история":       "история: до → после. Конкретный человек, конкретный результат",
        "провокация":    "провокационный тезис + аргумент + неожиданный вывод",
        "польза":        "полезный разбор: один приём, механизм действия, как применить",
        "мини-практика": "одно упражнение которое можно сделать прямо сейчас — шаги + эффект",
        "сравнение":     "два подхода или два упражнения — коротко и по делу",
    }
    hint = _fmt_hints.get(fmt.lower(), fmt)
    length_hint = "до 900 символов" if platform == "tg" else "600–1000 символов"

    user_prompt = (
        f"{_current_date_hint()}\n\n"
        f"Тема поста: {topic}\n"
        f"День в контент-плане: {day}\n"
        f"Формат: {hint}\n\n"
        f"Структура: сильный крючок (1–2 строки) → 2–3 абзаца → призыв к действию.\n"
        f"Пустая строка между абзацами. Каждый абзац начинается с эмодзи. "
        f"Длина: {length_hint}. Только текст поста, без пояснений."
    )

    await q.edit_message_text(f"⏳ Генерирую пост для {plat_label}…")

    try:
        text = await asyncio.to_thread(_generate_text, user_prompt, POST_SYSTEM_PROMPT, 1500)
        photo = await _get_photo("qigong tai chi meditation")

        db_platform = {"tg": "post_tg", "vk": "post_vk", "both": "post_both"}.get(platform, "post_tg")
        post_id = save_post(db_platform, text, photo, scheduled_at=None)

        USER_GENERATED[uid] = {
            "post_text": text,
            "photo_bytes": photo,
            "platform": platform,
            "post_id": post_id,
        }
        USER_STATE.pop(uid, None)

        kb = _publish_keyboard(platform)
        await _reply_preview(q.message, photo, text, kb)

    except Exception as e:
        logger.error(f"_generate_plan_post error: {e}", exc_info=True)
        await q.message.reply_text(f"❌ Ошибка генерации: {e}", reply_markup=_main_keyboard())


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
        "Панель управления каналом 🌿 Оздоровительный цигун и ушу",
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

    # ── YouTube ───────────────────────────────────────────────────────────────
    elif data == "youtube_menu":
        yt_ok = _yt.is_authorized()
        status_line = "Канал: Дыхание движения\nСтатус: " + ("✅ авторизован" if yt_ok else "⚠️ не авторизован — нажми «Авторизоваться»")
        await _safe_edit(q, f"▶️ YouTube\n\n{status_line}", reply_markup=_youtube_keyboard())

    elif data == "yt_auth":
        if not _yt.is_configured():
            await _safe_edit(q,
                "❌ Не заданы переменные окружения:\n"
                "YOUTUBE_CLIENT_ID\nYOUTUBE_CLIENT_SECRET\nYOUTUBE_REDIRECT_URI\n\n"
                "Добавь их в Railway и перезапусти бот.",
                reply_markup=_back_keyboard("youtube_menu"),
            )
            return
        if _yt.is_authorized():
            await _safe_edit(
                q, "✅ YouTube уже авторизован.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔁 Обновить права доступа", callback_data="yt_reauth")],
                    [InlineKeyboardButton("◀ Назад", callback_data="youtube_menu")],
                ]),
            )
            return
        url = _yt.get_auth_url()
        await _safe_edit(q,
            f"Для авторизации YouTube:\n\n"
            f"1. Перейди по ссылке:\n{url}\n\n"
            f"2. Войди в Google-аккаунт канала «Дыхание движения»\n"
            f"3. После авторизации будешь перенаправлен на дашборд\n\n"
            f"Авторизация автоматически сохранится.",
            reply_markup=_back_keyboard("youtube_menu"),
        )

    elif data == "yt_reauth":
        # Пере-авторизация нужна после добавления нового scope (например,
        # youtube.force-ssl для правки уже опубликованных видео) — старый
        # refresh_token не даёт новых прав задним числом.
        url = _yt.get_auth_url()
        await _safe_edit(q,
            f"Повторная авторизация (обновит права доступа):\n\n"
            f"1. Перейди по ссылке:\n{url}\n\n"
            f"2. Войди в тот же Google-аккаунт канала «Дыхание движения»\n"
            f"3. Подтверди новый набор разрешений\n\n"
            f"Старая авторизация будет заменена новой.",
            reply_markup=_back_keyboard("youtube_menu"),
        )

    elif data == "yt_analytics":
        await _safe_edit(q, "📊 Загружаю аналитику...", reply_markup=_back_keyboard("youtube_menu"))
        report = await asyncio.to_thread(_yt_master.get_analytics_report)
        await ctx.bot.send_message(uid, f"📊 Аналитика канала\n\n{report}",
                                   reply_markup=_youtube_keyboard())

    elif data == "yt_prepare_shorts":
        USER_GENERATED[uid] = {"yt_format": "shorts"}
        USER_STATE[uid] = "yt_awaiting_topic"
        await _safe_edit(q,
            "✍️ Введи тему Shorts (до 60 сек). Примеры:\n\n"
            "• «Одно упражнение Бадуаньцзин — разведение лука»\n"
            "• «Почему цигун помогает при стрессе»\n"
            "• «3 дыхательных цикла перед сном»",
            reply_markup=_back_keyboard("youtube_menu"),
        )

    elif data == "yt_prepare_video":
        USER_GENERATED[uid] = {"yt_format": "lesson"}
        USER_STATE[uid] = "yt_awaiting_topic"
        await _safe_edit(q,
            "🎬 Введи тему полного видео. Примеры:\n\n"
            "• «Бадуаньцзин полный комплекс для начинающих»\n"
            "• «У Цинь Си — пять упражнений животных»\n"
            "• «Что такое цигун и с чего начать»",
            reply_markup=_back_keyboard("youtube_menu"),
        )

    elif data == "yt_history":
        rows = get_youtube_videos(15)
        if not rows:
            await _safe_edit(q, "История видео пуста.", reply_markup=_back_keyboard("youtube_menu"))
            return
        lines = []
        btns = []
        for vid_id, yt_id, title, fmt, status, views, likes, pub_at, cr_at in rows:
            cr_msk = (cr_at + timedelta(hours=3)).strftime("%d.%m %H:%M") if cr_at else "?"
            yt_link = f"https://youtu.be/{yt_id}" if yt_id else "—"
            icon = "✅" if status == "published" else "📝"
            lines.append(f"{icon} {cr_msk} [{fmt}]\n{title}\n{yt_link}\n👁 {views} ❤️ {likes}")
            if status == "published" and yt_id:
                btns.append([InlineKeyboardButton(f"✏️ {title[:40]}", callback_data=f"yt_editpub_{vid_id}")])
        btns.append([InlineKeyboardButton("◀ Назад", callback_data="youtube_menu")])
        await _safe_edit(q, "\n\n".join(lines), reply_markup=InlineKeyboardMarkup(btns))

    elif data == "yt_editpub_byurl":
        USER_STATE[uid] = "yt_editpub_awaiting_url"
        await _safe_edit(
            q,
            "Пришли ссылку на видео (youtu.be/..., youtube.com/watch?v=... или /shorts/...) "
            "или просто ID видео. Работает для ЛЮБОГО видео на канале — даже если оно "
            "не отслеживается ботом (например, загружено вручную через Studio).",
            reply_markup=_back_keyboard("youtube_menu"),
        )

    elif data == "yt_editpub_title":
        USER_STATE[uid] = "yt_editpub_awaiting_title"
        await _safe_edit(q, "Пришли новое название видео (до 100 символов).",
                         reply_markup=_back_keyboard("yt_history"))

    elif data == "yt_editpub_desc":
        USER_STATE[uid] = "yt_editpub_awaiting_desc"
        await _safe_edit(q, "Пришли новый текст описания целиком (замени полностью, включая ссылки).",
                         reply_markup=_back_keyboard("yt_history"))

    elif data == "yt_addplaylist":
        yt_id = USER_GENERATED.get(uid, {}).get("yt_editpub_ytid")
        if not yt_id:
            await _safe_edit(q, "❌ Не нашёл видео — открой карточку заново.",
                             reply_markup=_back_keyboard("yt_history"))
            return
        await _safe_edit(q, "⏳ Добавляю в плейлист...", reply_markup=None)
        try:
            playlist_id = await _get_or_create_baduanjin_playlist()
            existing = await asyncio.to_thread(_yt.get_playlist_item_video_ids, playlist_id)
            if yt_id in existing:
                await ctx.bot.send_message(uid, "Видео уже есть в плейлисте «Бадуаньцзин за 21 день».",
                                           reply_markup=_youtube_keyboard())
            else:
                await asyncio.to_thread(_yt.add_playlist_item, playlist_id, yt_id)
                await ctx.bot.send_message(
                    uid, f"✅ Добавлено в плейлист «Бадуаньцзин за 21 день»:\nhttps://youtu.be/{yt_id}",
                    reply_markup=_youtube_keyboard(),
                )
        except Exception as e:
            logger.error(f"add_playlist_item error: {e}", exc_info=True)
            await ctx.bot.send_message(uid, f"❌ Не удалось добавить в плейлист: {e}",
                                       reply_markup=_youtube_keyboard())

    elif data == "yt_playlist_backfill":
        await _safe_edit(q, "⏳ Собираю плейлист «Бадуаньцзин за 21 день»...", reply_markup=None)
        try:
            playlist_id = await _get_or_create_baduanjin_playlist()
            existing = await asyncio.to_thread(_yt.get_playlist_item_video_ids, playlist_id)
            added, skipped, failed = [], [], []
            for vid in _BADUANJIN_BACKFILL_IDS:
                if vid in existing:
                    skipped.append(vid)
                    continue
                try:
                    await asyncio.to_thread(_yt.add_playlist_item, playlist_id, vid)
                    added.append(vid)
                except Exception as e:
                    logger.error(f"backfill add {vid} error: {e}", exc_info=True)
                    failed.append(vid)
            lines = [f"📃 Плейлист «Бадуаньцзин за 21 день»: https://www.youtube.com/playlist?list={playlist_id}"]
            if added:
                lines.append(f"✅ Добавлено: {len(added)} — {', '.join(added)}")
            if skipped:
                lines.append(f"⏭ Уже было: {len(skipped)} — {', '.join(skipped)}")
            if failed:
                lines.append(f"❌ Не удалось: {len(failed)} — {', '.join(failed)}")
            await ctx.bot.send_message(uid, "\n".join(lines), reply_markup=_youtube_keyboard())
        except Exception as e:
            logger.error(f"playlist_backfill error: {e}", exc_info=True)
            await ctx.bot.send_message(uid, f"❌ Не удалось собрать плейлист: {e}",
                                       reply_markup=_youtube_keyboard())

    elif data.startswith("yt_editpub_"):
        video_db_id = int(data.replace("yt_editpub_", ""))
        row = await asyncio.to_thread(get_youtube_video, video_db_id)
        if not row:
            await _safe_edit(q, "Видео не найдено в БД.", reply_markup=_back_keyboard("yt_history"))
            return
        _, yt_id, title, description, tags, fmt, script, thumb, status = row
        if not yt_id:
            await _safe_edit(q, "Видео ещё не опубликовано на YouTube.", reply_markup=_back_keyboard("yt_history"))
            return
        try:
            text, kb = await _render_editpub_detail(uid, yt_id, video_db_id)
        except Exception as e:
            await _safe_edit(q, f"❌ Не удалось загрузить текущие данные с YouTube: {e}",
                             reply_markup=_back_keyboard("yt_history"))
            return
        await _safe_edit(q, text, reply_markup=kb)

    elif data.startswith("yt_pub_"):
        # Публикация подготовленного видео на YouTube
        video_db_id = int(data.replace("yt_pub_", ""))
        gen = USER_GENERATED.get(uid, {})
        video_path = gen.get("video_path")
        if not video_path or not Path(video_path).exists():
            await ctx.bot.send_message(uid, "❌ Видео не найдено. Отрендери заново.",
                                       reply_markup=_youtube_keyboard())
            return
        await ctx.bot.send_message(uid, "⏳ Загружаю видео на YouTube...")
        try:
            yt_id = await asyncio.to_thread(
                _yt_master.publish_prepared, video_path, gen.get("yt_prepared", {})
            )
            await mark_youtube_published(video_db_id, yt_id)
            await ctx.bot.send_message(
                uid,
                f"✅ Видео опубликовано!\nhttps://youtu.be/{yt_id}",
                reply_markup=_youtube_keyboard(),
            )
        except Exception as e:
            await ctx.bot.send_message(uid, f"❌ Ошибка загрузки: {e}",
                                       reply_markup=_youtube_keyboard())

    # ── История постов ────────────────────────────────────────────────────────
    elif data == "posts_history":
        rows = get_posts_history(20)
        if not rows:
            await q.edit_message_text("История пуста.", reply_markup=_back_keyboard())
            return
        lines = []
        for pid, platform, text, published, sched_at, created_at in rows:
            created_msk = (created_at + timedelta(hours=3)).strftime("%d.%m %H:%M") if created_at else "?"
            if published:
                status = "✅ опубликован"
            elif sched_at:
                sched_msk = (sched_at + timedelta(hours=3)).strftime("%d.%m %H:%M")
                status = f"📅 запланирован на {sched_msk}"
            else:
                status = "📝 черновик"
            snippet = (text or "")[:80]
            lines.append(f"#{pid} [{platform}] {created_msk}\n{status}\n{snippet}…")
        await q.edit_message_text("\n\n".join(lines), reply_markup=_back_keyboard())

    elif data == "growth_menu":
        text, kb = await _render_growth_menu()
        await _safe_edit(q, text, reply_markup=kb)

    elif data.startswith("gt_section_"):
        section = data.replace("gt_section_", "")
        text, kb = await _render_growth_section(section)
        await _safe_edit(q, text, reply_markup=kb)

    elif data.startswith("gt_toggle_"):
        rest = data.replace("gt_toggle_", "")
        task_id_str, _, section = rest.partition("_")
        await asyncio.to_thread(toggle_growth_task, int(task_id_str))
        if section:
            text, kb = await _render_growth_section(section)
        else:
            text, kb = await _render_growth_menu()
        await _safe_edit(q, text, reply_markup=kb)

    elif data == "dialogs_menu":
        text, kb = await _render_dialogs_menu(uid)
        await _safe_edit(q, text, reply_markup=kb)

    elif data.startswith("dlg_view_"):
        idx = int(data.replace("dlg_view_", ""))
        dlg_list = USER_GENERATED.get(uid, {}).get("dlg_list", [])
        if idx >= len(dlg_list):
            await _safe_edit(q, "Список устарел — открой «Диалоги клиентов» заново.",
                             reply_markup=_back_keyboard())
            return
        row_user_id, source = dlg_list[idx]
        history = await asyncio.to_thread(load_chat_history, row_user_id, source)
        platform = "Telegram" if source == "telegram" else "VK"
        if not history:
            await _safe_edit(q, f"Переписка [{platform}] {row_user_id} пуста.",
                             reply_markup=_back_keyboard("dialogs_menu"))
            return
        lines = [f"💬 Диалог [{platform}] ID {row_user_id}:\n"]
        for msg in history:
            role = "👤 Клиент" if msg.get("role") == "user" else "🤖 Конфуций"
            lines.append(f"{role}: {msg.get('content', '')}")
        full_text = "\n\n".join(lines)
        chunks = [full_text[i:i + 3800] for i in range(0, len(full_text), 3800)]
        for i, chunk in enumerate(chunks):
            kb = _back_keyboard("dialogs_menu") if i == len(chunks) - 1 else None
            await ctx.bot.send_message(uid, chunk, reply_markup=kb)

    elif data.startswith("yt_show_script_"):
        video_db_id = int(data.replace("yt_show_script_", ""))
        gen = USER_GENERATED.get(uid, {})
        script = gen.get("yt_prepared", {}).get("script", "")
        if not script:
            await _safe_edit(q, "Сценарий не найден.", reply_markup=_back_keyboard("youtube_menu"))
            return
        # Разбиваем длинный сценарий на части по 3800 символов
        chunks = [script[i:i+3800] for i in range(0, len(script), 3800)]
        for i, chunk in enumerate(chunks):
            prefix = f"📜 Сценарий (часть {i+1}/{len(chunks)}):\n\n" if len(chunks) > 1 else "📜 Сценарий:\n\n"
            if i == len(chunks) - 1:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Редактировать сценарий", callback_data="yt_edit_script")],
                    [InlineKeyboardButton("◀ YouTube-меню", callback_data="youtube_menu")],
                ])
            else:
                kb = None
            await ctx.bot.send_message(uid, prefix + chunk, reply_markup=kb)

    elif data == "yt_edit_script":
        gen = USER_GENERATED.get(uid, {})
        if not gen.get("yt_prepared"):
            await _safe_edit(q, "Сначала подготовь видео.", reply_markup=_youtube_keyboard())
            return
        USER_STATE[uid] = "yt_awaiting_script_edit"
        await _safe_edit(
            q,
            "Отправь новый текст сценария. Старый сценарий будет заменён полностью.",
            reply_markup=_back_keyboard("youtube_menu"),
        )

    elif data.startswith("yt_show_desc_"):
        video_db_id = int(data.replace("yt_show_desc_", ""))
        gen = USER_GENERATED.get(uid, {})
        description = gen.get("yt_prepared", {}).get("description", "")
        if not description:
            await _safe_edit(q, "Описание не найдено.", reply_markup=_back_keyboard("youtube_menu"))
            return
        chunks = [description[i:i+3800] for i in range(0, len(description), 3800)]
        for i, chunk in enumerate(chunks):
            prefix = f"📝 Описание (часть {i+1}/{len(chunks)}):\n\n" if len(chunks) > 1 else "📝 Описание:\n\n"
            if i == len(chunks) - 1:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Редактировать описание", callback_data="yt_edit_desc")],
                    [InlineKeyboardButton("◀ YouTube-меню", callback_data="youtube_menu")],
                ])
            else:
                kb = None
            await ctx.bot.send_message(uid, prefix + chunk, reply_markup=kb)

    elif data == "yt_edit_desc":
        gen = USER_GENERATED.get(uid, {})
        if not gen.get("yt_prepared"):
            await _safe_edit(q, "Сначала подготовь видео.", reply_markup=_youtube_keyboard())
            return
        USER_STATE[uid] = "yt_awaiting_desc_edit"
        await _safe_edit(
            q,
            "Отправь новый текст описания. Старое описание будет заменено полностью.",
            reply_markup=_back_keyboard("youtube_menu"),
        )

    elif data == "yt_upload_prompt":
        await _safe_edit(q,
            "📤 Загрузи видеофайл (.mp4) ответным сообщением.\n\n"
            "Если файл больше 20 МБ — Telegram не даёт боту его скачать напрямую. "
            "В этом случае загрузи видео на Яндекс.Диск или Google Диск, открой доступ по ссылке "
            "и пришли эту ссылку сюда текстом — бот скачает файл сам.\n\n"
            "Бот загрузит его на YouTube с подготовленным заголовком, описанием и превью.",
            reply_markup=_back_keyboard("youtube_menu"),
        )
        USER_STATE[uid] = "yt_awaiting_video_file"

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
                [InlineKeyboardButton("✍️ Свой шаблон",        callback_data="reel_custom")],
                [InlineKeyboardButton("🎞 Смонтировать клипы", callback_data="reel_montage")],
                [InlineKeyboardButton("◀ Назад",               callback_data="main_menu")],
            ]),
        )

    elif data == "reel_montage":
        USER_GENERATED[uid] = {"clip_paths": []}
        USER_STATE[uid] = "collecting_clips"
        await q.edit_message_text(
            "🎞 <b>Монтаж из клипов</b>\n\n"
            "Присылай видеофайлы по одному, в том порядке, в котором их нужно склеить. "
            "Когда закончишь — нажми «Готово».",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="montage_cancel")],
            ]),
        )

    elif data == "montage_cancel":
        gen = USER_GENERATED.get(uid, {})
        for p in gen.get("clip_paths", []):
            Path(p).unlink(missing_ok=True)
        USER_STATE.pop(uid, None)
        USER_GENERATED.pop(uid, None)
        await ctx.bot.send_message(uid, "Монтаж отменён.", reply_markup=_main_keyboard())

    elif data == "montage_finish":
        gen = USER_GENERATED.get(uid, {})
        clips = gen.get("clip_paths", [])
        if len(clips) < 2:
            await ctx.bot.send_message(uid, "Нужно минимум 2 клипа, чтобы было что склеивать. Пришли ещё видео.")
            return
        USER_STATE.pop(uid, None)
        wait_msg = await ctx.bot.send_message(uid, f"⏳ Склеиваю {len(clips)} клипов...")
        try:
            out = tempfile.mktemp(suffix=".mp4")
            await asyncio.to_thread(concat_clips, clips, out)
            gen["video_path"] = out
            gen["caption"] = "Смонтированное видео"
            USER_GENERATED[uid] = gen
            for p in clips:
                Path(p).unlink(missing_ok=True)
            try:
                await wait_msg.delete()
            except Exception:
                pass
            with open(out, "rb") as f:
                await ctx.bot.send_video(
                    uid, video=f, supports_streaming=True,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Telegram",   callback_data="reel_pub_tg")],
                        [InlineKeyboardButton("📤 ВКонтакте", callback_data="reel_pub_vk")],
                        [InlineKeyboardButton("📤 Обе",        callback_data="reel_pub_both")],
                        [InlineKeyboardButton("◀ Отмена",      callback_data="main_menu")],
                    ]),
                    caption="Видео смонтировано. Куда публикуем?"
                )
        except Exception as e:
            logger.error(f"Montage error: {e}", exc_info=True)
            await ctx.bot.send_message(uid, f"Ошибка склейки: {e}", reply_markup=_main_keyboard())

    elif data == "reel_motivation":
        USER_GENERATED[uid] = {"reel_type": "motivation"}
        USER_STATE[uid] = "reel_motivation"
        await q.edit_message_text(
            "🌟 <b>Мотивационный Reel</b>\n\n"
            "Напиши цитату или мысль (1–2 строки):\n\n"
            "<i>Например: Цигун в парке — это не спорт. Это возвращение к себе.</i>",
            parse_mode="HTML",
            reply_markup=_back_keyboard("create_reel"),
        )

    elif data == "reel_promo":
        USER_GENERATED[uid] = {"reel_type": "promo"}
        USER_STATE[uid] = "reel_promo"
        await q.edit_message_text(
            "🎁 <b>Промо-Reel (акция)</b>\n\n"
            "Напиши через <code>|</code>:\n"
            "<code>заголовок | цена | подпись</code>\n\n"
            "Примеры:\n"
            "• <code>Пробное занятие | 500 р. | Первый шаг к здоровью</code>\n"
            "• <code>Летняя акция | 3 000 р. | Июнь — июль</code>\n\n"
            "Или напиши <code>стандарт</code> — шаблон по умолчанию.",
            parse_mode="HTML",
            reply_markup=_back_keyboard("create_reel"),
        )

    elif data == "reel_announcement":
        USER_GENERATED[uid] = {"reel_type": "announcement"}
        USER_STATE[uid] = "reel_announcement"
        await q.edit_message_text(
            "📢 <b>Анонс занятия</b>\n\n"
            "Напиши через <code>/</code>:\n\n"
            "<b>Обязательно:</b> <code>Занятие / Тренер / Дата / Время</code>\n"
            "<b>Необязательно:</b> добавь <code>/Места/Подпись</code> в конце\n\n"
            "<i>Примеры:</i>\n"
            "<code>Цигун Пяти животных / Сергей / 5 июля / 10:00</code>\n\n"
            "<code>Бадуаньцзин / Сергей / суббота / 9:00 / 8 мест / Парк Сосновка</code>",
            parse_mode="HTML",
            reply_markup=_back_keyboard("create_reel"),
        )

    elif data == "reel_custom":
        USER_GENERATED[uid] = {"reel_type": "custom"}
        USER_STATE[uid] = "reel_custom"
        await q.edit_message_text(
            "✍️ <b>Свой шаблон</b>\n\n"
            "Напиши любой текст — он появится по центру экрана.\n\n"
            "Хочешь добавить подпись внизу — добавь <code>|</code> и текст:\n"
            "<code>Главный текст | мелкая подпись</code>\n\n"
            "<i>Примеры:</i>\n"
            "<code>Тренировки в парке Сосновка</code>\n"
            "<code>по субботам и вторникам!</code>\n\n"
            "<code>Занятие по цигун | 7 июля в 10:00</code>",
            parse_mode="HTML",
            reply_markup=_back_keyboard("create_reel"),
        )

    # ── Видео-фон для Reel ────────────────────────────────────────────────────
    elif data == "reel_bg_upload":
        USER_STATE[uid] = "awaiting_reel_bg_upload"
        await q.edit_message_text(
            "Отправь .mp4 файл как видео-фон для этого Reel.\n\n"
            "Видео будет использовано только для текущего Reel и не сохранится в библиотеку.\n"
            "Если хочешь сохранить — загрузи через Медиа → Видео-фон.",
            reply_markup=_back_keyboard("create_reel"),
        )

    elif data.startswith("reel_bg_"):
        choice = data.replace("reel_bg_", "")
        gen = USER_GENERATED.get(uid, {})
        if choice in ("none", "gradient"):
            gen["bg_video"] = None
        else:
            try:
                idx = int(choice)
                opts = gen.get("reel_video_options", [])
                p = opts[idx] if 0 <= idx < len(opts) else None
                gen["bg_video"] = p if p and Path(p).exists() else None
            except (ValueError, IndexError):
                gen["bg_video"] = None
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
            try:
                idx = int(choice)
                opts = gen.get("reel_audio_options", [])
                p = opts[idx] if 0 <= idx < len(opts) else None
                gen["audio_path"] = p if p and Path(p).exists() else None
            except (ValueError, IndexError):
                gen["audio_path"] = None
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
            wait_msg = await ctx.bot.send_message(uid, "⏳ Загружаю видео в VK...")
            vk_ok, vk_err = await _vk_clip(video_path)
            try:
                await wait_msg.delete()
            except Exception:
                pass
            if not vk_ok:
                await ctx.bot.send_message(uid, f"⚠️ VK Клип не удалось опубликовать.\n\nПричина: {vk_err}")
            else:
                sent = True
        if sent:
            caption = gen.get("caption", "Reel")
            db_platform = {"tg": "post_tg", "vk": "post_vk", "both": "post_both"}.get(platform, "post_tg")
            post_id = save_post(db_platform, f"[Reel] {caption}", photo_bytes=None, scheduled_at=None)
            mark_post_published(post_id)
        msg = "✅ Reel опубликован!" if sent else "⚠️ Не удалось опубликовать."
        # Используем send_message — edit_message_text может истечь пока идёт загрузка в VK
        await ctx.bot.send_message(uid, msg, reply_markup=_main_keyboard())

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
        try:
            month = datetime.now().strftime("%Y-%m")
            plan = await asyncio.to_thread(get_content_plan, month)
            if not plan:
                await q.edit_message_text(
                    "Плана на этот месяц нет. Сначала создай его.",
                    reply_markup=_back_keyboard("content_plan"),
                )
                return
            plan_id, items_json, approved = plan
            if isinstance(items_json, list):
                items = items_json
            elif isinstance(items_json, str):
                try:
                    items = json.loads(items_json)
                except Exception:
                    items = []
            else:
                items = []
            CONTENT_PLANS[uid] = {"items": items, "plan_id": plan_id}

            if approved:
                await _show_plan_items(q, uid, plan_id, items)
            else:
                text = _format_plan_text(items, month)
                if len(text) > 4000:
                    text = text[:4000] + "\n…"
                await q.edit_message_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=_plan_keyboard(plan_id),
                )
        except Exception as e:
            logger.error(f"cp_view error: {e}", exc_info=True)
            await q.edit_message_text(
                f"❌ Ошибка загрузки плана: {e}",
                reply_markup=_back_keyboard("content_plan"),
            )

    elif data.startswith("cp_approve_"):
        try:
            plan_id = int(data.replace("cp_approve_", ""))
            await asyncio.to_thread(approve_content_plan, plan_id)
            # Берём пункты из кеша или из БД
            cached = CONTENT_PLANS.get(uid, {})
            items = cached.get("items")
            if not items:
                month = datetime.now().strftime("%Y-%m")
                plan = await asyncio.to_thread(get_content_plan, month)
                if plan:
                    _, items_json, _ = plan
                    if isinstance(items_json, list):
                        items = items_json
                    elif isinstance(items_json, str):
                        items = json.loads(items_json)
                    else:
                        items = []
                    CONTENT_PLANS[uid] = {"items": items, "plan_id": plan_id}
                else:
                    items = []
            await _show_plan_items(q, uid, plan_id, items)
        except Exception as e:
            logger.error(f"cp_approve_ error: {e}", exc_info=True)
            await q.edit_message_text(
                f"❌ Ошибка при утверждении плана: {e}",
                reply_markup=_back_keyboard("content_plan"),
            )

    elif data == "cp_regen":
        USER_STATE[uid] = "waiting_plan_feedback"
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(
            "Что изменить в плане? Напиши пожелания:\n\n"
            "<i>Например: добавь больше анонсов занятий, убери провокации, сделай темы короче</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("← Оставить как есть", callback_data="cp_view")],
            ]),
        )

    elif data.startswith("plan_item_"):
        # Пользователь выбрал конкретный пункт плана
        try:
            _, _, plan_id_s, idx_s = data.split("_", 3)
            plan_id, idx = int(plan_id_s), int(idx_s)
        except ValueError:
            await q.answer("Ошибка данных")
            return
        cached = CONTENT_PLANS.get(uid, {})
        items = cached.get("items")
        if not items:
            plan = await asyncio.to_thread(get_content_plan, datetime.now().strftime("%Y-%m"))
            if not plan:
                await q.message.reply_text("План не найден. Создай новый.", reply_markup=_main_keyboard())
                return
            _, items_json, _ = plan
            if isinstance(items_json, list):
                items = items_json
            elif isinstance(items_json, str):
                items = json.loads(items_json)
            else:
                items = []
            CONTENT_PLANS[uid] = {"items": items, "plan_id": plan_id}
        if idx >= len(items):
            await q.message.reply_text("Элемент не найден. Открой план заново.", reply_markup=_main_keyboard())
            return
        item = items[idx]
        topic = item.get("topic", "—")
        fmt = item.get("format", "—")
        await q.edit_message_text(
            f"📌 День {item.get('day','?')}: {topic}\nФормат: {fmt}\n\nВыбери платформу:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✈️ Telegram",   callback_data=f"plan_gen_tg_{plan_id}_{idx}")],
                [InlineKeyboardButton("📱 ВКонтакте", callback_data=f"plan_gen_vk_{plan_id}_{idx}")],
                [InlineKeyboardButton("🌐 Обе",        callback_data=f"plan_gen_both_{plan_id}_{idx}")],
                [InlineKeyboardButton("◀ К списку",     callback_data="cp_view")],
            ]),
        )

    elif data.startswith("plan_gen_"):
        # plan_gen_{platform}_{plan_id}_{idx}
        try:
            parts = data.split("_")
            platform = parts[2]
            plan_id = int(parts[3])
            idx = int(parts[4])
        except (IndexError, ValueError):
            await q.answer("Ошибка данных")
            return
        cached = CONTENT_PLANS.get(uid, {})
        items = cached.get("items")
        if not items:
            plan = await asyncio.to_thread(get_content_plan, datetime.now().strftime("%Y-%m"))
            if not plan:
                await q.message.reply_text("План не найден. Создай новый.", reply_markup=_main_keyboard())
                return
            _, items_json, _ = plan
            if isinstance(items_json, list):
                items = items_json
            elif isinstance(items_json, str):
                items = json.loads(items_json)
            else:
                items = []
            CONTENT_PLANS[uid] = {"items": items, "plan_id": plan_id}
        if idx >= len(items):
            await q.message.reply_text("Элемент не найден.", reply_markup=_main_keyboard())
            return
        await _generate_plan_post(q, uid, items[idx], platform)

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
        audios = (list(AUDIO_DIR.glob("*.mp3")) + list(AUDIO_DIR.glob("*.aac"))
                  + list(AUDIO_DIR.glob("*.m4a")) + list(AUDIO_DIR.glob("*.ogg")))
        photos = list(PHOTOS_DIR.glob("*.jpg")) + list(PHOTOS_DIR.glob("*.png"))
        has_logo = LOGO_PATH.exists()
        await q.edit_message_text(
            f"Медиа-библиотека:\n"
            f"🖼 Фото для постов: {len(photos)}\n"
            f"🎥 Видео-фоны: {len(videos)}\n"
            f"🎵 Аудио-треки: {len(audios)}\n"
            f"🏷 Логотип: {'есть' if has_logo else 'не загружен'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Загрузить фото для постов", callback_data="upload_post_photo")],
                [InlineKeyboardButton("📤 Загрузить видео-фон",       callback_data="upload_video")],
                [InlineKeyboardButton("📤 Загрузить аудио-трек",      callback_data="upload_audio")],
                [InlineKeyboardButton("📤 Загрузить логотип",         callback_data="upload_logo")],
                [InlineKeyboardButton("🗑 Удалить фото",              callback_data="delete_photo_menu")],
                [InlineKeyboardButton("🗑 Удалить видео-фон",         callback_data="delete_video_menu")],
                [InlineKeyboardButton("🗑 Удалить аудио-трек",        callback_data="delete_audio_menu")],
                [InlineKeyboardButton("◀ Назад",                      callback_data="main_menu")],
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

    elif data == "upload_post_photo":
        USER_STATE[uid] = "awaiting_post_photo"
        await q.edit_message_text(
            "Отправь фото (JPEG или PNG) для использования в постах.\n"
            "Можно загрузить несколько — бот будет выбирать случайное.",
            reply_markup=_back_keyboard("media_menu"),
        )

    elif data == "delete_photo_menu":
        rows = get_post_photos()
        if not rows:
            await q.edit_message_text("Нет загруженных фото.",
                                      reply_markup=_back_keyboard("media_menu"))
            return
        btns = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_photo_{pid}")]
                for pid, name, _ in rows]
        btns.append([InlineKeyboardButton("◀ Назад", callback_data="media_menu")])
        await q.edit_message_text("Выбери фото для удаления:",
                                  reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("del_photo_"):
        photo_id = int(data.replace("del_photo_", ""))
        rows = get_post_photos()
        for pid, name, _ in rows:
            if pid == photo_id:
                delete_post_photo(photo_id)
                p = PHOTOS_DIR / name
                if p.exists():
                    p.unlink()
                break
        await q.edit_message_text("✅ Фото удалено.", reply_markup=_back_keyboard("media_menu"))

    elif data == "delete_video_menu":
        rows = get_bg_videos_meta()
        if not rows:
            await q.edit_message_text("Нет загруженных видео-фонов.",
                                      reply_markup=_back_keyboard("media_menu"))
            return
        btns = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_video_{vid_id}")]
                for vid_id, name in rows]
        btns.append([InlineKeyboardButton("◀ Назад", callback_data="media_menu")])
        await q.edit_message_text("Выбери видео для удаления:",
                                  reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("del_video_"):
        vid_id = int(data.replace("del_video_", ""))
        rows = get_bg_videos_meta()
        for row_id, name in rows:
            if row_id == vid_id:
                delete_bg_video(vid_id)
                p = VIDEOS_DIR / name
                if p.exists():
                    p.unlink()
                break
        await q.edit_message_text("✅ Видео удалено.", reply_markup=_back_keyboard("media_menu"))

    elif data == "delete_audio_menu":
        rows = get_bg_audios_meta()
        if not rows:
            await q.edit_message_text("Нет загруженных аудио-треков.",
                                      reply_markup=_back_keyboard("media_menu"))
            return
        btns = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_audio_{aud_id}")]
                for aud_id, name in rows]
        btns.append([InlineKeyboardButton("◀ Назад", callback_data="media_menu")])
        await q.edit_message_text("Выбери аудио для удаления:",
                                  reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("del_audio_"):
        aud_id = int(data.replace("del_audio_", ""))
        rows = get_bg_audios_meta()
        for row_id, name in rows:
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
            await _safe_edit(q, "Текст не найден.", reply_markup=_main_keyboard())
            return
        ok_tg = ok_vk = True
        if platform in ("tg", "both"):
            try:
                await _publish_post_to_tg(ctx.bot, text, photo)
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
            msg = "✅ Опубликовано!" + (f"\n⚠️ {vk_err}" if vk_err else "")
        elif vk_err:
            msg = f"⚠️ Ошибка VK: {vk_err}"
        else:
            msg = "⚠️ Частично опубликовано."
        await _safe_edit(q, msg, reply_markup=_main_keyboard())

    elif data.startswith("pub_sched_"):
        platform = data.replace("pub_sched_", "")
        USER_GENERATED[uid]["sched_platform"] = platform
        USER_STATE[uid] = "awaiting_schedule_time"
        await _safe_edit(
            q,
            "Введи дату и время публикации (МСК):\n"
            "Формат: ДД.ММ ЧЧ:ММ\n"
            "Пример: 28.06 10:00",
            reply_markup=_back_keyboard(),
        )

    elif data == "pub_regenerate":
        gen = USER_GENERATED.get(uid, {})
        topic = gen.get("post_topic", "")
        if not topic:
            await _safe_edit(q, "Тема не найдена.", reply_markup=_main_keyboard())
            return
        USER_STATE[uid] = "generating_post"
        await _safe_edit(q, "Генерирую заново...")
        await _do_generate_post(update, ctx, uid, topic)

    elif data == "pub_change_photo":
        gen = USER_GENERATED.get(uid, {})
        if not gen.get("post_text"):
            await _safe_edit(q, "Сначала создай пост.", reply_markup=_main_keyboard())
            return
        USER_STATE[uid] = "awaiting_post_photo_replace"
        await _safe_edit(
            q,
            "Отправь новое фото для поста (JPEG или PNG).\n\n"
            "Можно отправить как фото или как файл.",
            reply_markup=_back_keyboard(),
        )

    elif data == "pub_edit_text":
        gen = USER_GENERATED.get(uid, {})
        if not gen.get("post_text"):
            await _safe_edit(q, "Сначала создай пост.", reply_markup=_main_keyboard())
            return
        USER_STATE[uid] = "awaiting_post_text_edit"
        await _safe_edit(
            q,
            "Отправь новый текст поста. Старый текст будет заменён полностью.",
            reply_markup=_back_keyboard(),
        )

    elif data == "pub_platform_choice":
        await q.edit_message_text(
            "Где опубликовать?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Telegram",   callback_data="pub_now_tg")],
                [InlineKeyboardButton("ВКонтакте", callback_data="pub_now_vk")],
                [InlineKeyboardButton("Обе",        callback_data="pub_now_both")],
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

    # Состояния, которые ожидают только текст — игнорируем нетекстовые сообщения
    _TEXT_ONLY_STATES = {
        "awaiting_post_topic", "awaiting_cp_topic", "waiting_plan_feedback",
        "awaiting_sub_name", "awaiting_sub_url", "awaiting_sub_desc",
        "awaiting_schedule_time", "reel_motivation", "reel_promo", "reel_announcement",
        "reel_custom", "awaiting_post_text_edit", "yt_awaiting_topic",
        "yt_awaiting_script_edit", "yt_awaiting_desc_edit",
        "yt_editpub_awaiting_title", "yt_editpub_awaiting_desc", "yt_editpub_awaiting_url",
    }
    if state in _TEXT_ONLY_STATES:
        if not update.message or not update.message.text:
            await update.message.reply_text("Отправь текстовое сообщение.")
            return

    # ── YouTube: тема видео ───────────────────────────────────────────────────
    if state == "yt_awaiting_topic":
        topic = update.message.text.strip()
        fmt   = USER_GENERATED.get(uid, {}).get("yt_format", "shorts")
        USER_STATE[uid] = "yt_generating"
        await update.message.reply_text(
            f"⏳ Готовлю {'Shorts' if fmt == 'shorts' else 'видео'}: «{topic}»\n"
            f"Пишу сценарий, SEO и превью — это займёт ~30 секунд..."
        )
        try:
            prepared = await asyncio.to_thread(
                _yt_master.prepare_video, topic, fmt
            )
            USER_GENERATED[uid]["yt_prepared"] = prepared
            USER_STATE[uid] = "yt_ready"

            # Сохраняем в БД
            video_db_id = await asyncio.to_thread(
                save_youtube_video,
                prepared["title"],
                prepared["description"],
                ", ".join(prepared.get("tags", [])),
                prepared["format"],
                prepared["script"],
                prepared.get("thumbnail_bytes"),
            )
            USER_GENERATED[uid]["yt_db_id"] = video_db_id

            # Превью
            thumb = prepared.get("thumbnail_bytes")
            caption = (
                f"✅ Готово!\n\n"
                f"📌 Заголовок:\n{prepared['title']}\n\n"
                f"📝 Описание (первые строки):\n{prepared['description'][:300]}...\n\n"
                f"🏷 Теги: {', '.join(prepared.get('tags', [])[:6])}"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📜 Показать сценарий", callback_data=f"yt_show_script_{video_db_id}")],
                [InlineKeyboardButton("✏️ Редактировать сценарий", callback_data="yt_edit_script")],
                [InlineKeyboardButton("📝 Показать описание", callback_data=f"yt_show_desc_{video_db_id}")],
                [InlineKeyboardButton("✏️ Редактировать описание", callback_data="yt_edit_desc")],
                [InlineKeyboardButton("📤 Загрузить видео и опубликовать", callback_data="yt_upload_prompt")],
                [InlineKeyboardButton("◀ YouTube-меню",       callback_data="youtube_menu")],
            ])
            if thumb:
                await ctx.bot.send_photo(uid, photo=BytesIO(thumb), caption=caption, reply_markup=kb)
            else:
                await ctx.bot.send_message(uid, caption, reply_markup=kb)

        except Exception as e:
            logger.error(f"YoutubeMaster error: {e}", exc_info=True)
            await ctx.bot.send_message(uid, f"❌ Ошибка подготовки видео: {e}",
                                       reply_markup=_youtube_keyboard())
            USER_STATE.pop(uid, None)
        return

    # ── YouTube: редактирование сценария ──────────────────────────────────────
    if state == "yt_awaiting_script_edit":
        new_script = update.message.text.strip()
        gen = USER_GENERATED.setdefault(uid, {})
        gen.setdefault("yt_prepared", {})["script"] = new_script
        USER_STATE.pop(uid, None)
        video_db_id = gen.get("yt_db_id")
        if video_db_id:
            await asyncio.to_thread(update_youtube_video_script, video_db_id, new_script)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Загрузить видео и опубликовать", callback_data="yt_upload_prompt")],
            [InlineKeyboardButton("◀ YouTube-меню", callback_data="youtube_menu")],
        ])
        await update.message.reply_text("✅ Сценарий обновлён.", reply_markup=kb)
        return

    # ── YouTube: редактирование описания ──────────────────────────────────────
    if state == "yt_awaiting_desc_edit":
        new_desc = update.message.text.strip()
        gen = USER_GENERATED.setdefault(uid, {})
        gen.setdefault("yt_prepared", {})["description"] = new_desc
        USER_STATE.pop(uid, None)
        video_db_id = gen.get("yt_db_id")
        if video_db_id:
            await asyncio.to_thread(update_youtube_video_description, video_db_id, new_desc)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Загрузить видео и опубликовать", callback_data="yt_upload_prompt")],
            [InlineKeyboardButton("◀ YouTube-меню", callback_data="youtube_menu")],
        ])
        await update.message.reply_text("✅ Описание обновлено.", reply_markup=kb)
        return

    # ── YouTube: правка названия уже опубликованного видео ────────────────────
    if state == "yt_editpub_awaiting_title":
        new_title = update.message.text.strip()
        gen = USER_GENERATED.get(uid, {})
        yt_id = gen.get("yt_editpub_ytid")
        video_db_id = gen.get("yt_editpub_id")
        USER_STATE.pop(uid, None)
        if not yt_id:
            await update.message.reply_text("❌ Не нашёл видео — открой «История видео» или пришли ссылку заново.",
                                            reply_markup=_youtube_keyboard())
            return
        try:
            await asyncio.to_thread(_yt.update_video, yt_id, title=new_title)
            if video_db_id:
                await asyncio.to_thread(update_youtube_video_title, video_db_id, new_title)
            await update.message.reply_text(
                f"✅ Название обновлено на YouTube:\nhttps://youtu.be/{yt_id}\n{new_title}",
                reply_markup=_youtube_keyboard(),
            )
        except Exception as e:
            logger.error(f"update_video (title) error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Не удалось обновить название: {e}",
                                            reply_markup=_youtube_keyboard())
        return

    # ── YouTube: правка описания уже опубликованного видео ────────────────────
    if state == "yt_editpub_awaiting_desc":
        new_desc = update.message.text.strip()
        gen = USER_GENERATED.get(uid, {})
        yt_id = gen.get("yt_editpub_ytid")
        video_db_id = gen.get("yt_editpub_id")
        USER_STATE.pop(uid, None)
        if not yt_id:
            await update.message.reply_text("❌ Не нашёл видео — открой «История видео» или пришли ссылку заново.",
                                            reply_markup=_youtube_keyboard())
            return
        try:
            await asyncio.to_thread(_yt.update_video, yt_id, description=new_desc)
            if video_db_id:
                await asyncio.to_thread(update_youtube_video_description, video_db_id, new_desc)
            await update.message.reply_text(
                f"✅ Описание обновлено на YouTube:\nhttps://youtu.be/{yt_id}",
                reply_markup=_youtube_keyboard(),
            )
        except Exception as e:
            logger.error(f"update_video (description) error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Не удалось обновить описание: {e}",
                                            reply_markup=_youtube_keyboard())
        return

    # ── YouTube: правка по ссылке (видео может быть не в БД вообще) ────────────
    if state == "yt_editpub_awaiting_url":
        yt_id = _extract_youtube_id(update.message.text)
        if not yt_id:
            # Состояние НЕ сбрасываем — бот остаётся в ожидании ссылки, чтобы
            # следующее (уже правильное) сообщение не пролетело мимо молча.
            await update.message.reply_text(
                "Не смог распознать ссылку/ID видео. Пришли вид youtu.be/XXXXXXXXXXX, "
                "youtube.com/watch?v=XXXXXXXXXXX, /shorts/XXXXXXXXXXX или голый 11-символьный ID.",
            )
            return
        USER_STATE.pop(uid, None)
        row = await asyncio.to_thread(get_youtube_video_by_ytid, yt_id)
        video_db_id = row[0] if row else None
        try:
            text, kb = await _render_editpub_detail(uid, yt_id, video_db_id)
        except Exception as e:
            logger.error(f"get_video_snippet error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Не удалось загрузить видео с YouTube: {e}",
                                            reply_markup=_youtube_keyboard())
            return
        if video_db_id is None:
            text += "\n\n(это видео не отслеживается ботом — правка уйдёт напрямую на YouTube, без записи в БД)"
        await update.message.reply_text(text, reply_markup=kb)
        return

    # ── Тема поста ────────────────────────────────────────────────────────────
    if state == "awaiting_post_topic":
        topic = update.message.text.strip()
        USER_GENERATED[uid]["post_topic"] = topic
        USER_STATE[uid] = "generating_post"
        await update.message.reply_text("Генерирую пост...")
        await _do_generate_post(update, ctx, uid, topic)

    # ── Тема контент-плана ────────────────────────────────────────────────────
    elif state == "awaiting_cp_topic":
        try:
            focus = update.message.text.strip()
            month = USER_GENERATED.get(uid, {}).get("cp_month", datetime.now().strftime("%Y-%m"))
            await update.message.reply_text("Создаю контент-план...")
            prompt = (
                f"Создай контент-план на месяц {month} для Telegram-канала и группы ВКонтакте "
                f"об оздоровительном цигун и ушу. Фокус месяца: {focus or 'общее оздоровление'}. "
                "30 постов. Верни ТОЛЬКО JSON."
            )
            raw = await asyncio.to_thread(
                _generate_text, prompt, CONTENT_PLAN_SYSTEM_PROMPT, 3000
            )
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start < 0 or end <= start:
                raise ValueError(
                    f"AI вернул пустой или некорректный ответ. Первые 200 символов: {raw[:200]!r}"
                )
            try:
                items = json.loads(raw[start:end])
            except json.JSONDecodeError as je:
                raise ValueError(f"Ошибка парсинга JSON: {je}. Фрагмент: {raw[start:start+300]!r}")
            plan_id = await asyncio.to_thread(
                save_content_plan, month, json.dumps(items, ensure_ascii=False)
            )
            CONTENT_PLANS[uid] = {"items": items, "plan_id": plan_id}
            text = _format_plan_text(items, month)
            if len(text) > 4000:
                text = text[:4000] + "\n…"
            await update.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=_plan_keyboard(plan_id),
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}", reply_markup=_back_keyboard())
        finally:
            USER_STATE.pop(uid, None)

    # ── Переделать контент-план с пожеланиями ────────────────────────────────
    elif state == "waiting_plan_feedback":
        feedback = update.message.text.strip()
        USER_STATE.pop(uid, None)
        month = datetime.now().strftime("%Y-%m")
        await update.message.reply_text("Переделываю план с учётом пожеланий...")
        try:
            prompt = (
                f"Создай контент-план на месяц {month} для Telegram-канала и группы ВКонтакте "
                f"об оздоровительном цигун и ушу. "
                f"Пожелания: {feedback}. "
                "30 постов. Верни ТОЛЬКО JSON."
            )
            raw = await asyncio.to_thread(
                _generate_text, prompt, CONTENT_PLAN_SYSTEM_PROMPT, 3000
            )
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start < 0 or end <= start:
                raise ValueError(f"AI вернул некорректный ответ: {raw[:200]!r}")
            items = json.loads(raw[start:end])
            plan_id = await asyncio.to_thread(
                save_content_plan, month, json.dumps(items, ensure_ascii=False)
            )
            CONTENT_PLANS[uid] = {"items": items, "plan_id": plan_id}
            text = _format_plan_text(items, month)
            if len(text) > 4000:
                text = text[:4000] + "\n…"
            await update.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=_plan_keyboard(plan_id),
            )
        except Exception as e:
            logger.error(f"waiting_plan_feedback error: {e}", exc_info=True)
            await update.message.reply_text(f"Ошибка: {e}", reply_markup=_back_keyboard())

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
                f"✅ Пост #{post_id} запланирован на {msk_str} МСК.\n\n"
                "Найдёшь его в «📋 История постов» со статусом 📅.",
                reply_markup=_main_keyboard(),
            )
        except ValueError:
            await update.message.reply_text(
                "Неверный формат. Введи: ДД.ММ ЧЧ:ММ\nПример: 28.06 10:00"
            )

    # ── Reel: мотивация (1 шаг) ──────────────────────────────────────────────
    elif state == "reel_motivation":
        quote = update.message.text.strip()
        USER_STATE.pop(uid, None)
        gen = USER_GENERATED.get(uid, {})
        gen.update({"quote": quote, "subtext": "@baguazhangspb", "caption": quote})
        USER_GENERATED[uid] = gen
        await _ask_bg_video(update, ctx, uid)

    # ── Reel: промо ──────────────────────────────────────────────────────────
    elif state == "reel_promo":
        import re as _re
        raw = update.message.text.strip()
        DEFAULT_CTA = "@baguazhangspb"
        if raw.lower() in ("стандарт", "default", ""):
            headline, price, subtext, cta = "Пробное занятие", "500 р.", "Первое занятие ни к чему не обязывает", DEFAULT_CTA
        elif "|" in raw:
            parts = [p.strip() for p in raw.split("|")]
            headline = parts[0] if len(parts) > 0 else ""
            price    = parts[1] if len(parts) > 1 else ""
            subtext  = parts[2] if len(parts) > 2 else ""
            cta      = parts[3] if len(parts) > 3 else DEFAULT_CTA
        elif "/" in raw:
            parts = [p.strip() for p in raw.split("/")]
            headline = parts[0] if len(parts) > 0 else ""
            price    = parts[1] if len(parts) > 1 else ""
            subtext  = parts[2] if len(parts) > 2 else ""
            cta      = parts[3] if len(parts) > 3 else DEFAULT_CTA
        else:
            price_m = _re.search(r'(\d[\d\s]*)\s*(?:рублей?|руб\.?|р\.?|₽)', raw, _re.IGNORECASE)
            if price_m:
                price    = price_m.group(1).strip() + " р."
                headline = (raw[:price_m.start()] + raw[price_m.end():]).strip().strip(".,!|")
                subtext  = ""
            else:
                headline, price, subtext = raw, "", ""
            cta = DEFAULT_CTA
        gen = USER_GENERATED.get(uid, {})
        gen.update({"headline": headline, "price": price, "subtext": subtext, "cta": cta,
                    "caption": f"{headline} — {price}"})
        USER_GENERATED[uid] = gen
        USER_STATE.pop(uid, None)
        await _ask_bg_video(update, ctx, uid)

    # ── Reel: анонс занятия ───────────────────────────────────────────────────
    elif state == "reel_announcement":
        parts = [p.strip() for p in update.message.text.split("/")]
        if len(parts) < 4:
            await update.message.reply_text(
                "Нужно минимум 4 части:\n"
                "<code>Занятие / Тренер / Дата / Время</code>\n\nПопробуй ещё раз.",
                parse_mode="HTML",
            )
            return
        USER_STATE.pop(uid, None)
        gen = USER_GENERATED.get(uid, {})
        gen.update({
            "class_type": parts[0],
            "trainer":    parts[1],
            "date":       parts[2],
            "time":       parts[3],
            "spots":      parts[4] if len(parts) > 4 else "",
            "tagline":    parts[5] if len(parts) > 5 else "",
            "caption":    f"{parts[0]} · {parts[1]} · {parts[2]} {parts[3]}",
        })
        USER_GENERATED[uid] = gen
        await _ask_bg_video(update, ctx, uid)

    # ── Reel: свой шаблон ────────────────────────────────────────────────────
    elif state == "reel_custom":
        raw = update.message.text.strip()
        if "|" in raw:
            parts = raw.split("|", 1)
            quote   = parts[0].strip()
            subtext = parts[1].strip()
        else:
            quote   = raw
            subtext = "@baguazhangspb"
        USER_STATE.pop(uid, None)
        gen = USER_GENERATED.get(uid, {})
        gen.update({"quote": quote, "subtext": subtext, "caption": quote})
        USER_GENERATED[uid] = gen
        await _ask_bg_video(update, ctx, uid)

    # ── Сбор клипов для монтажа ───────────────────────────────────────────────
    elif state == "collecting_clips":
        if not (update.message.document or update.message.video):
            await update.message.reply_text("Отправь видеофайл, либо нажми «Готово».")
            return
        doc = update.message.document or update.message.video
        try:
            file = await doc.get_file()
            local_path = tempfile.mktemp(suffix=".mp4")
            await file.download_to_drive(local_path)
            gen = USER_GENERATED.setdefault(uid, {"clip_paths": []})
            gen.setdefault("clip_paths", []).append(local_path)
            USER_GENERATED[uid] = gen
            n = len(gen["clip_paths"])
            await update.message.reply_text(
                f"Добавлено ({n}). Пришли ещё видео или нажми «Готово».",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Готово, смонтировать ({n})", callback_data="montage_finish")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="montage_cancel")],
                ]),
            )
        except Exception as e:
            logger.error(f"Ошибка загрузки клипа: {e}")
            await update.message.reply_text(f"Не удалось загрузить видео: {e}")

    # ── Загрузка видео-фона ───────────────────────────────────────────────────
    elif state == "awaiting_bg_video":
        if not (update.message.document or update.message.video):
            await update.message.reply_text("Отправь .mp4 файл.")
            return
        doc = update.message.document or update.message.video
        fname = getattr(doc, "file_name", None) or "bg_video.mp4"
        if not fname.lower().endswith(".mp4"):
            fname = "bg_video.mp4"
        try:
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
        except Exception as e:
            logger.error(f"Ошибка загрузки видео: {e}")
            await update.message.reply_text(
                f"Не удалось загрузить видео: {e}\n\n"
                "Попробуй отправить файл меньшего размера (до 20 МБ) или как документ.",
                reply_markup=_back_keyboard("media_menu"),
            )

    # ── Загрузка фона для текущего Reel (одноразовый, не сохраняется) ────────
    elif state == "awaiting_reel_bg_upload":
        if not (update.message.document or update.message.video):
            await update.message.reply_text("Отправь .mp4 файл как видео или документ.")
            return
        doc = update.message.document or update.message.video
        fname = getattr(doc, "file_name", None) or "reel_bg_tmp.mp4"
        if not fname.lower().endswith(".mp4"):
            fname = "reel_bg_tmp.mp4"
        try:
            file = await doc.get_file()
            buf = io.BytesIO()
            await file.download_to_memory(buf)
            data = buf.getvalue()
            # Сохраняем во временный файл (не в БД)
            tmp_path = VIDEOS_DIR / f"_tmp_{uid}_{fname}"
            tmp_path.write_bytes(data)
            gen = USER_GENERATED.get(uid, {})
            gen["bg_video"] = str(tmp_path)
            gen["_reel_bg_tmp"] = str(tmp_path)  # пометка для удаления после рендера
            USER_GENERATED[uid] = gen
            USER_STATE.pop(uid, None)
            await _ask_audio(update, ctx, uid, gen)
        except Exception as e:
            logger.error(f"Ошибка загрузки Reel-фона: {e}")
            await update.message.reply_text(
                f"Не удалось загрузить: {e}\n\nПопробуй файл меньшего размера или отправь как документ.",
                reply_markup=_back_keyboard("create_reel"),
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

    # ── Загрузка фото для постов ──────────────────────────────────────────────
    elif state == "awaiting_post_photo":
        photo = update.message.photo
        doc   = update.message.document
        if photo:
            file = await photo[-1].get_file()
            fname = f"photo_{file.file_unique_id}.jpg"
        elif doc:
            file = await doc.get_file()
            fname = getattr(doc, "file_name", None) or f"photo_{file.file_unique_id}.jpg"
        else:
            await update.message.reply_text("Отправь фото (JPEG или PNG).")
            return
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data = buf.getvalue()
        save_post_photo(fname, data)
        p = PHOTOS_DIR / fname
        p.write_bytes(data)
        photos_total = len(list(PHOTOS_DIR.glob("*.jpg")) + list(PHOTOS_DIR.glob("*.png")))
        await update.message.reply_text(
            f"✅ Фото загружено. Всего в библиотеке: {photos_total}.\n"
            "Можешь отправить ещё или вернись в меню.",
            reply_markup=_back_keyboard("media_menu"),
        )

    # ── Замена фото для текущего поста ───────────────────────────────────────
    elif state == "awaiting_post_photo_replace":
        photo_msg = update.message.photo
        doc = update.message.document
        if photo_msg:
            file = await photo_msg[-1].get_file()
        elif doc and (getattr(doc, "file_name", "") or "").lower().endswith((".jpg", ".jpeg", ".png")):
            file = await doc.get_file()
        else:
            await update.message.reply_text("Отправь фото (JPEG или PNG).")
            return
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        new_photo = buf.getvalue()
        gen = USER_GENERATED.get(uid, {})
        gen["photo_bytes"] = new_photo
        USER_GENERATED[uid] = gen
        USER_STATE.pop(uid, None)
        platform = gen.get("platform", "post_tg").replace("post_", "")
        post_text = gen.get("post_text", "")
        await _reply_preview(update.message, new_photo, post_text, _publish_keyboard(platform))

    # ── Редактирование текста поста ───────────────────────────────────────────
    elif state == "awaiting_post_text_edit":
        if not update.message.text:
            await update.message.reply_text("Отправь текстовое сообщение с новым текстом поста.")
            return
        new_text = update.message.text.strip()
        gen = USER_GENERATED.get(uid, {})
        gen["post_text"] = new_text
        USER_GENERATED[uid] = gen
        USER_STATE.pop(uid, None)
        platform = gen.get("platform", "post_tg").replace("post_", "")
        photo = gen.get("photo_bytes")
        await _reply_preview(update.message, photo, new_text, _publish_keyboard(platform))

    # ── YouTube: получение видеофайла для загрузки ────────────────────────────
    elif state == "yt_awaiting_video_file":
        msg = update.message
        video = msg.video or msg.document
        video_url = None
        if not video:
            text = (msg.text or "").strip()
            if text.startswith("http"):
                video_url = text
            else:
                await msg.reply_text(
                    "Отправь видеофайл (.mp4) или ссылку на Яндекс.Диск/Google Диск, "
                    "если файл больше 20 МБ.",
                    reply_markup=_back_keyboard("youtube_menu"))
                return
        gen = USER_GENERATED.get(uid, {})
        prepared = gen.get("yt_prepared")
        video_db_id = gen.get("yt_db_id")
        if not prepared:
            await msg.reply_text("Сначала подготовь видео (тема → сценарий → SEO).",
                                 reply_markup=_youtube_keyboard())
            USER_STATE.pop(uid, None)
            return

        await msg.reply_text("⬇️ Скачиваю видеофайл...")
        try:
            tmp_path = Path(tempfile.mkdtemp()) / "yt_upload.mp4"
            if video_url:
                await _download_video_from_url(video_url, tmp_path)
            else:
                tg_file = await ctx.bot.get_file(video.file_id)
                await tg_file.download_to_drive(str(tmp_path))

            await ctx.bot.send_message(uid, "⏳ Загружаю на YouTube...")
            yt_id = await asyncio.to_thread(
                _yt_master.publish_prepared, str(tmp_path), prepared
            )
            if video_db_id:
                await asyncio.to_thread(mark_youtube_published, video_db_id, yt_id)

            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

            USER_STATE.pop(uid, None)
            await ctx.bot.send_message(
                uid,
                f"✅ Видео опубликовано!\nhttps://youtu.be/{yt_id}",
                reply_markup=_youtube_keyboard(),
            )
        except Exception as e:
            logger.error(f"YouTube upload error: {e}", exc_info=True)
            await ctx.bot.send_message(uid, f"❌ Ошибка загрузки: {e}",
                                       reply_markup=_youtube_keyboard())
            USER_STATE.pop(uid, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Генерация поста
# ═══════════════════════════════════════════════════════════════════════════════

def _current_date_hint() -> str:
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=3)))
    months_ru = ["января","февраля","марта","апреля","мая","июня",
                 "июля","августа","сентября","октября","ноября","декабря"]
    m = now.month
    season = ("зима" if m in (12,1,2) else "весна" if m in (3,4,5)
              else "лето" if m in (6,7,8) else "осень")
    return f"Сегодня {now.day} {months_ru[m-1]} {now.year} ({season})."


async def _do_generate_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            uid: int, topic: str):
    try:
        date_hint = _current_date_hint()
        text = await asyncio.to_thread(
            _generate_text,
            f"{date_hint}\n\nНапиши пост для канала о цигун и ушу на тему: {topic}",
        )
        photo = await _get_photo("qigong tai chi meditation")
        gen = USER_GENERATED.get(uid, {})
        gen.update({"post_text": text, "photo_bytes": photo, "post_topic": topic})
        USER_GENERATED[uid] = gen
        USER_STATE.pop(uid, None)

        platform = gen.get("platform", "post_tg").replace("post_", "")
        kb = _publish_keyboard(platform)
        await _preview_post(ctx.bot, uid, photo, text, kb)
    except Exception as e:
        await ctx.bot.send_message(uid, f"Ошибка генерации: {e}",
                                   reply_markup=_main_keyboard())
        USER_STATE.pop(uid, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Reel: выбор фона / аудио / длительности / рендер
# ═══════════════════════════════════════════════════════════════════════════════

async def _ask_bg_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    videos = sorted(VIDEOS_DIR.glob("*.mp4"))
    gen = USER_GENERATED.get(uid, {})
    gen["reel_video_options"] = [str(v) for v in videos[:8]]
    USER_GENERATED[uid] = gen
    btns = [[InlineKeyboardButton("🎨 Градиент (без видео)", callback_data="reel_bg_gradient")]]
    btns.append([InlineKeyboardButton("📤 Загрузить своё видео", callback_data="reel_bg_upload")])
    for i, v in enumerate(videos[:8]):
        btns.append([InlineKeyboardButton(f"🎥 {v.stem}", callback_data=f"reel_bg_{i}")])
    btns.append([InlineKeyboardButton("◀ Назад", callback_data="create_reel")])
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_text("🎨 Выбери видео-фон для Reel:", reply_markup=InlineKeyboardMarkup(btns))


async def _ask_audio(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int, gen: dict):
    audios = sorted(
        list(AUDIO_DIR.glob("*.mp3")) + list(AUDIO_DIR.glob("*.aac"))
        + list(AUDIO_DIR.glob("*.m4a")) + list(AUDIO_DIR.glob("*.ogg"))
    )
    gen["reel_audio_options"] = [str(a) for a in audios[:8]]
    USER_GENERATED[uid] = gen
    btns = [[InlineKeyboardButton("🔇 Без аудио", callback_data="reel_audio_none")]]
    if gen.get("bg_video"):
        btns.append([InlineKeyboardButton("🎵 Аудио из видео-фона", callback_data="reel_audio_from_video")])
    for i, a in enumerate(audios[:8]):
        btns.append([InlineKeyboardButton(f"🎵 {Path(a).stem}", callback_data=f"reel_audio_{i}")])
    btns.append([InlineKeyboardButton("◀ Назад", callback_data="create_reel")])
    kb = InlineKeyboardMarkup(btns)
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text("🎵 Выбери аудио для Reel:", reply_markup=kb)
            return
        except Exception:
            pass
    await ctx.bot.send_message(uid, "🎵 Выбери аудио для Reel:", reply_markup=kb)


async def _ask_duration(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("15 сек", callback_data="reel_dur_15")],
        [InlineKeyboardButton("30 сек", callback_data="reel_dur_30")],
        [InlineKeyboardButton("60 сек", callback_data="reel_dur_60")],
    ])
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text("Выбери длительность Reel:", reply_markup=kb)
            return
        except Exception:
            pass
    await ctx.bot.send_message(uid, "Выбери длительность Reel:", reply_markup=kb)


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
        if reel_type in ("motivation", "custom"):
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

        # Удаляем временный фон, если был загружен одноразово
        tmp_bg = gen.get("_reel_bg_tmp")
        if tmp_bg:
            try:
                Path(tmp_bg).unlink(missing_ok=True)
            except Exception:
                pass

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

def _ensure_youtube_plan():
    """Вставляет 30-видео YouTube контент-план один раз, если его ещё нет."""
    MONTH = "youtube-plan"
    try:
        if get_content_plan(MONTH):
            return
        plan = [
            {"day": 1,  "topic": "Что такое цигун и зачем он нужен современному человеку", "format": "обучающий"},
            {"day": 2,  "topic": "Цигун за 10 минут для начинающих — первый комплекс", "format": "урок"},
            {"day": 3,  "topic": "Почему болит спина — и как цигун это исправляет", "format": "объяснение"},
            {"day": 4,  "topic": "5 упражнений цигун от боли в шее за 7 минут", "format": "shorts+урок"},
            {"day": 5,  "topic": "Утренняя практика цигун 15 минут — зарядись на весь день", "format": "урок"},
            {"day": 6,  "topic": "Базовая стойка ушу — с чего начать", "format": "урок"},
            {"day": 7,  "topic": "Дыхательная практика цигун для снятия стресса", "format": "урок"},
            {"day": 8,  "topic": "Цигун для позвоночника — комплекс Бадуаньцзинь", "format": "урок"},
            {"day": 9,  "topic": "Что изменилось за 30 дней практики цигун — мой опыт", "format": "история"},
            {"day": 10, "topic": "Цигун vs йога — в чём разница и что выбрать", "format": "сравнение"},
            {"day": 11, "topic": "Полный комплекс Бадуаньцзинь — 8 упражнений", "format": "урок 30 мин"},
            {"day": 12, "topic": "Цигун для суставов — колени, тазобедренные, плечи", "format": "урок"},
            {"day": 13, "topic": "Медитация в движении — базовый комплекс У Цинь Си", "format": "урок"},
            {"day": 14, "topic": "Как правильно дышать в цигун — главная ошибка новичков", "format": "обучающий"},
            {"day": 15, "topic": "Цигун перед сном — расслабление за 10 минут", "format": "урок"},
            {"day": 16, "topic": "Ответы на вопросы: боль, прогресс, сколько заниматься", "format": "разбор"},
            {"day": 17, "topic": "Техника «Три нити» ушу для координации", "format": "урок ушу"},
            {"day": 18, "topic": "Цигун при сидячей работе — прямо за столом", "format": "урок"},
            {"day": 19, "topic": "Разбор ошибок новичков в цигун", "format": "обзор"},
            {"day": 20, "topic": "Мой путь в цигун — как я начал и к чему пришёл", "format": "история"},
            {"day": 21, "topic": "Онлайн-занятие цигун вживую — присоединяйся", "format": "прямой эфир"},
            {"day": 22, "topic": "Программа «Здоровая спина за 21 день» — обзор курса", "format": "продающий"},
            {"day": 23, "topic": "Отзыв ученика: после 2 месяцев цигун ушла боль в спине", "format": "соцдоказательство"},
            {"day": 24, "topic": "Индивидуальные тренировки — кому подходят и как записаться", "format": "продающий"},
            {"day": 25, "topic": "Цигун для похудения — правда и мифы", "format": "трафик"},
            {"day": 26, "topic": "Утренний марафон цигун 7 дней — присоединяйся бесплатно", "format": "лид-магнит"},
            {"day": 27, "topic": "Результаты марафона — истории участников", "format": "итоги"},
            {"day": 28, "topic": "Детский цигун — можно ли заниматься с детьми", "format": "урок"},
            {"day": 29, "topic": "Вопрос-ответ: стоимость, расписание, как записаться", "format": "прозрачность"},
            {"day": 30, "topic": "Год практики цигун — главные изменения в жизни", "format": "итог+призыв"},
        ]
        plan_id = save_content_plan(MONTH, json.dumps(plan, ensure_ascii=False))
        approve_content_plan(plan_id)
        logger.info(f"YouTube контент-план загружен (id={plan_id})")
    except Exception as e:
        logger.error(f"_ensure_youtube_plan failed: {e}")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """Глобальный перехват ошибок — без него упавшая кнопка молчит, будто не работает."""
    logger.error("Ошибка в обработчике", exc_info=ctx.error)
    err_text = f"⚠️ Ошибка: {ctx.error}"
    try:
        for admin_id in ADMIN_IDS:
            await ctx.bot.send_message(admin_id, err_text[:4000])
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление об ошибке: {e}")


async def post_init(app: Application):
    try:
        init_db()
    except Exception as e:
        logger.error(f"init_db failed (non-fatal): {e}")
    _ensure_youtube_plan()
    asyncio.create_task(_restore_media())
    asyncio.create_task(_scheduled_post_loop(app.bot))
    asyncio.create_task(_growth_reminder_loop(app.bot))
    asyncio.create_task(_yt_digest_loop(app.bot))
    logger.info("admin_bot запущен v4")


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
