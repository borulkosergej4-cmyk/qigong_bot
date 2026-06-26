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
        conn.commit()


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

def save_content_plan(month: str, items: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO content_plan (month, items) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (month, items),
            )
        conn.commit()


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
