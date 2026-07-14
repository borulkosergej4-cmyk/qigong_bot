import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "")


def get_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS generated_posts (
                    id SERIAL PRIMARY KEY,
                    platform TEXT,
                    text TEXT,
                    published BOOLEAN DEFAULT FALSE,
                    photo_bytes BYTEA,
                    scheduled_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                ALTER TABLE generated_posts
                ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS content_plan (
                    id SERIAL PRIMARY KEY,
                    month TEXT,
                    items JSON,
                    approved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            # Индекс для производительности (не уникальный — могут быть старые дубли по month)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS content_plan_month_idx ON content_plan (month)"
            )

            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscription_links (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS saved_bg_videos (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    data BYTEA NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS saved_bg_audios (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    data BYTEA NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_config (
                    key TEXT PRIMARY KEY,
                    data BYTEA,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS saved_post_photos (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    data BYTEA NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    id SERIAL PRIMARY KEY,
                    name TEXT,
                    phone TEXT,
                    source TEXT,
                    username TEXT,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_id TEXT
            """)
            cur.execute("""
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS lesson TEXT
            """)

            # YouTube-видео
            cur.execute("""
                CREATE TABLE IF NOT EXISTS youtube_videos (
                    id           SERIAL PRIMARY KEY,
                    youtube_id   TEXT,
                    title        TEXT,
                    description  TEXT,
                    tags         TEXT,
                    format       TEXT,
                    script       TEXT,
                    status       TEXT DEFAULT 'pending',
                    thumbnail    BYTEA,
                    views        INTEGER DEFAULT 0,
                    likes        INTEGER DEFAULT 0,
                    comments     INTEGER DEFAULT 0,
                    published_at TIMESTAMP,
                    created_at   TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                ALTER TABLE youtube_videos ADD COLUMN IF NOT EXISTS format TEXT
            """)

            # История диалога Конфуция — переживает рестарты Railway и визиты клиента
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    user_id    TEXT NOT NULL,
                    source     TEXT NOT NULL,
                    history    JSONB NOT NULL DEFAULT '[]',
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (user_id, source)
                )
            """)

            # Задачи стратегии роста («Бадуаньцзин за 21 день»)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS growth_tasks (
                    id         SERIAL PRIMARY KEY,
                    section    TEXT NOT NULL,
                    text       TEXT NOT NULL,
                    done       BOOLEAN DEFAULT FALSE,
                    sort_order INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
    _seed_growth_tasks()


# ── Стратегия роста ──────────────────────────────────────────────────────────

_GROWTH_TASKS_SEED = [
    ("launch", "Неделя 1 · Структурировать видео в 21-дневную прогрессию"),
    ("launch", "Неделя 1 · Снять недостающие видео"),
    ("launch", "Неделя 1 · Собрать PDF-схему движений"),
    ("launch", "Неделя 2 · Сообщить текущим подписчикам Boosty"),
    ("launch", "Неделя 2 · Пост в VK/TG от Сергея лично"),
    ("launch", "Неделя 2 · Сообщение через Конфуция интересовавшимся"),
    ("launch", "Неделя 3 · Серия YouTube Shorts-тизеров по упражнениям с CTA на Boosty"),
    ("launch", "Неделя 3 · Пост с первыми отзывами"),
    ("launch", "Неделя 4 · Живой Q&A с Сергеем для когорты"),
    ("launch", "Неделя 4 · Бесплатное очное занятие по итогам"),
    ("launch", "Неделя 4 · Решить — повторять когортами или сделать вечный доступ"),
    ("playbook", "Прямое сообщение текущим подписчикам Boosty"),
    ("playbook", "Догрев тех, кто писал Конфуцию, но не записался"),
    ("playbook", "YouTube Shorts-тизеры с CTA на Boosty"),
    ("playbook", "Пост от Сергея лично в VK/TG"),
    ("playbook", "Реферал для клиентов клуба — приведи друга, бесплатное занятие"),
    ("playbook", "Win-back по «зависшим» заявкам из bookings"),
    ("playbook", "Бартер с локальными блогерами про ТКМ/оздоровление"),
    ("playbook", "Комментарии-ценность в тематических пабликах про цигун/ушу"),
    ("playbook", "Личные сообщения активным подписчикам паблика"),
    ("playbook", "Кросс-промо со студией «Стремление»"),
    ("revenue", "Перенести персистентную историю диалога Конфуция из stremlenie_bot в qigong_bot"),
    ("revenue", "Сделать так, чтобы CTA на Boosty звучал в самом сценарии видео, не только в описании"),
    ("revenue", "Win-back по базе bookings"),
    ("revenue", "Перейти на когортный запуск вместо вечного доступа"),
    ("revenue", "Кросс-промо между «Стремлением» и цигун"),
]


def _seed_growth_tasks():
    """Синхронизирует таблицу со списком выше: убирает задачи, которых больше нет
    в плане, добавляет новые, обновляет порядок — но не трогает done у совпадающих
    по тексту задач, чтобы не сбрасывать отмеченный прогресс."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM growth_tasks")
            existing_texts = {row[0] for row in cur.fetchall()}
            seed_texts = {text for _, text in _GROWTH_TASKS_SEED}
            to_remove = existing_texts - seed_texts
            if to_remove:
                cur.execute("DELETE FROM growth_tasks WHERE text = ANY(%s)", (list(to_remove),))
            for i, (section, text) in enumerate(_GROWTH_TASKS_SEED):
                if text in existing_texts:
                    cur.execute(
                        "UPDATE growth_tasks SET sort_order=%s, section=%s WHERE text=%s",
                        (i, section, text),
                    )
                else:
                    cur.execute(
                        "INSERT INTO growth_tasks (section, text, sort_order) VALUES (%s, %s, %s)",
                        (section, text, i),
                    )
        conn.commit()


def get_growth_tasks():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, section, text, done FROM growth_tasks ORDER BY sort_order"
            )
            return cur.fetchall()


def toggle_growth_task(task_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE growth_tasks SET done = NOT done WHERE id=%s", (task_id,)
            )
        conn.commit()


def get_next_pending_task():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, section, text FROM growth_tasks WHERE done=FALSE "
                "ORDER BY sort_order LIMIT 1"
            )
            return cur.fetchone()


def get_last_growth_reminder_date() -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM bot_config WHERE key='growth_reminder_date'")
            row = cur.fetchone()
    return bytes(row[0]).decode() if row and row[0] else None


def set_last_growth_reminder_date(date_str: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_config (key, data, updated_at) VALUES ('growth_reminder_date', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data, updated_at=NOW()",
                (date_str.encode(),),
            )
        conn.commit()


def get_last_yt_digest_date() -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM bot_config WHERE key='yt_digest_date'")
            row = cur.fetchone()
    return bytes(row[0]).decode() if row and row[0] else None


def set_last_yt_digest_date(date_str: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_config (key, data, updated_at) VALUES ('yt_digest_date', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data, updated_at=NOW()",
                (date_str.encode(),),
            )
        conn.commit()


# ── История диалога Конфуция ─────────────────────────────────────────────────

def save_chat_history(user_id, source: str, history: list):
    import json
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO chat_history (user_id, source, history, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (user_id, source) DO UPDATE
                       SET history = EXCLUDED.history, updated_at = NOW()""",
                    (str(user_id), source, json.dumps(history, ensure_ascii=False)),
                )
            conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"save_chat_history error: {e}")


def load_chat_history(user_id, source: str) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT history FROM chat_history WHERE user_id = %s AND source = %s",
                    (str(user_id), source),
                )
                row = cur.fetchone()
        return row[0] if row and row[0] else []
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"load_chat_history error: {e}")
        return []


def clear_chat_history_db(user_id, source: str):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chat_history WHERE user_id = %s AND source = %s",
                    (str(user_id), source),
                )
            conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"clear_chat_history_db error: {e}")


def get_recent_chat_histories(limit: int = 15):
    """Для админского просмотра диалогов — список последних активных бесед
    (не полный текст, чтобы список грузился быстро)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, source, history, updated_at FROM chat_history "
                "ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


# ── Посты ─────────────────────────────────────────────────────────────────────

def save_post(platform: str, text: str, photo_bytes: bytes | None = None,
              scheduled_at=None) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO generated_posts (platform, text, photo_bytes, scheduled_at) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (platform, text, photo_bytes, scheduled_at),
            )
            post_id = cur.fetchone()[0]
        conn.commit()
    return post_id


def get_scheduled_posts():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, platform, text, photo_bytes, scheduled_at "
                "FROM generated_posts WHERE published=FALSE AND scheduled_at IS NOT NULL "
                "ORDER BY scheduled_at"
            )
            return cur.fetchall()


def mark_post_published(post_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generated_posts SET published=TRUE WHERE id=%s", (post_id,)
            )
        conn.commit()


def get_posts_history(limit: int = 50):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, platform, text, published, scheduled_at, created_at "
                "FROM generated_posts ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


# ── Контент-план ──────────────────────────────────────────────────────────────

def save_content_plan(month: str, items: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM content_plan WHERE month=%s ORDER BY created_at DESC LIMIT 1",
                (month,),
            )
            row = cur.fetchone()
            if row:
                plan_id = row[0]
                cur.execute(
                    "UPDATE content_plan SET items=%s, approved=FALSE WHERE id=%s",
                    (items, plan_id),
                )
            else:
                cur.execute(
                    "INSERT INTO content_plan (month, items) VALUES (%s, %s) RETURNING id",
                    (month, items),
                )
                plan_id = cur.fetchone()[0]
        conn.commit()
    return plan_id


def get_content_plan(month: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, items, approved FROM content_plan WHERE month=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (month,),
            )
            return cur.fetchone()


def approve_content_plan(plan_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE content_plan SET approved=TRUE WHERE id=%s", (plan_id,)
            )
        conn.commit()


# ── Ссылки на подписки ────────────────────────────────────────────────────────

def add_subscription_link(name: str, url: str, description: str = "") -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO subscription_links (name, url, description) "
                "VALUES (%s, %s, %s) RETURNING id",
                (name, url, description),
            )
            link_id = cur.fetchone()[0]
        conn.commit()
    return link_id


def get_subscription_links():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, url, description FROM subscription_links "
                "ORDER BY created_at"
            )
            return cur.fetchall()


def delete_subscription_link(link_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM subscription_links WHERE id=%s", (link_id,))
        conn.commit()


# ── Медиа (видео, аудио, логотип) ─────────────────────────────────────────────

def save_bg_video(name: str, data: bytes):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO saved_bg_videos (name, data) VALUES (%s, %s)",
                (name, data),
            )
        conn.commit()


def get_bg_videos():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, data FROM saved_bg_videos ORDER BY created_at")
            return cur.fetchall()


def get_bg_videos_meta():
    """Список без BYTEA data — для меню (не гонять видео по сети ради списка имён)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM saved_bg_videos ORDER BY created_at")
            return cur.fetchall()


def get_bg_video_data(video_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM saved_bg_videos WHERE id=%s", (video_id,))
            row = cur.fetchone()
            return row[0] if row else None


def delete_bg_video(video_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM saved_bg_videos WHERE id=%s", (video_id,))
        conn.commit()


def save_bg_audio(name: str, data: bytes):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO saved_bg_audios (name, data) VALUES (%s, %s)",
                (name, data),
            )
        conn.commit()


def get_bg_audios():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, data FROM saved_bg_audios ORDER BY created_at")
            return cur.fetchall()


def get_bg_audios_meta():
    """Список без BYTEA data — для меню (не гонять аудио по сети ради списка имён)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM saved_bg_audios ORDER BY created_at")
            return cur.fetchall()


def get_bg_audio_data(audio_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM saved_bg_audios WHERE id=%s", (audio_id,))
            row = cur.fetchone()
            return row[0] if row else None


def delete_bg_audio(audio_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM saved_bg_audios WHERE id=%s", (audio_id,))
        conn.commit()


def save_logo(data: bytes):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_config (key, data, updated_at) VALUES ('logo', %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data, updated_at=NOW()",
                (data,),
            )
        conn.commit()


def get_logo() -> bytes | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM bot_config WHERE key='logo'")
            row = cur.fetchone()
            return bytes(row[0]) if row else None


# ── Заявки на запись ──────────────────────────────────────────────────────────

def save_booking(name: str, phone: str, source: str, username: str = "",
                 user_id: str = "", lesson: str = "") -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bookings (name, phone, source, username, user_id, lesson) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (name, phone, source, username, user_id, lesson),
            )
            bid = cur.fetchone()[0]
        conn.commit()
    return bid


def get_bookings(limit: int = 50):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, phone, source, username, created_at "
                "FROM bookings ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


def get_bookings_stats():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM bookings")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM bookings "
                "WHERE created_at >= NOW() - INTERVAL '1 day'"
            )
            today = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bookings WHERE source='telegram'")
            tg = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bookings WHERE source='vk'")
            vk = cur.fetchone()[0]
        return {"total": total, "today": today, "telegram": tg, "vk": vk}


# ── Фото для постов ───────────────────────────────────────────────────────────

def save_post_photo(name: str, data: bytes):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO saved_post_photos (name, data) VALUES (%s, %s)",
                (name, data),
            )
        conn.commit()


def get_post_photos():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, data FROM saved_post_photos ORDER BY created_at")
            return cur.fetchall()


def delete_post_photo(photo_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM saved_post_photos WHERE id=%s", (photo_id,))
        conn.commit()


# ── YouTube-видео ─────────────────────────────────────────────────────────────

def save_youtube_video(
    title: str, description: str, tags: str, format_: str,
    script: str, thumbnail: bytes | None = None,
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO youtube_videos "
                "(title, description, tags, format, script, thumbnail, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING id",
                (title, description, tags, format_, script, thumbnail),
            )
            vid = cur.fetchone()[0]
        conn.commit()
    return vid


def update_youtube_video_script(video_db_id: int, script: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE youtube_videos SET script=%s WHERE id=%s",
                (script, video_db_id),
            )
        conn.commit()


def update_youtube_video_title(video_db_id: int, title: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE youtube_videos SET title=%s WHERE id=%s",
                (title, video_db_id),
            )
        conn.commit()


def update_youtube_video_description(video_db_id: int, description: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE youtube_videos SET description=%s WHERE id=%s",
                (description, video_db_id),
            )
        conn.commit()


def claim_youtube_video_for_upload(video_db_id: int) -> bool:
    """Атомарно переводит видео из 'pending' в 'uploading' — только если оно ещё 'pending'.
    Возвращает True, если ИМЕННО ЭТОТ вызов забрал право на загрузку, False — если видео уже
    захвачено другим процессом (или уже опубликовано).

    Нужно, потому что USER_STATE/USER_GENERATED — это память процесса, а не общая для всех
    инстансов бота: если во время редеплоя Railway на секунды-две одновременно живы старый и
    новый контейнер, оба поллят Telegram и могут получить одно и то же сообщение с видео —
    без проверки в БД оба запустили бы загрузку и видео опубликовалось бы на YouTube дважды."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE youtube_videos SET status='uploading' WHERE id=%s AND status='pending' RETURNING id",
                (video_db_id,),
            )
            claimed = cur.fetchone() is not None
        conn.commit()
    return claimed


def reset_youtube_video_status(video_db_id: int, status: str = "pending"):
    """Откатывает статус назад (например, после неудачной загрузки — чтобы можно было
    попробовать снова, а не застрять навсегда в 'uploading')."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE youtube_videos SET status=%s WHERE id=%s", (status, video_db_id))
        conn.commit()


def mark_youtube_published(video_db_id: int, youtube_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE youtube_videos SET status='published', youtube_id=%s, "
                "published_at=NOW() WHERE id=%s",
                (youtube_id, video_db_id),
            )
        conn.commit()


def get_youtube_videos(limit: int = 30):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, youtube_id, title, format, status, views, likes, "
                "published_at, created_at "
                "FROM youtube_videos ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


def get_youtube_video_by_ytid(youtube_id: str):
    """Найти запись по youtube_id — для правки видео по ссылке, когда неизвестен
    внутренний id (например, если запись пришла не из «Истории видео»)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, youtube_id, title, description, tags, format, script, "
                "thumbnail, status FROM youtube_videos WHERE youtube_id=%s",
                (youtube_id,),
            )
            return cur.fetchone()


def get_youtube_video(video_db_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, youtube_id, title, description, tags, format, script, "
                "thumbnail, status FROM youtube_videos WHERE id=%s",
                (video_db_id,),
            )
            return cur.fetchone()
