"""
YouTube Data API v3 — клиент для канала «Оздоровительный цигун и ушу».

OAuth2 flow:
  1. get_auth_url()        → ссылка для авторизации в браузере
  2. exchange_code(code)   → обменивает code → tokens, сохраняет в DB
  3. Все методы автоматически обновляют access_token через refresh_token.

Требует переменных окружения:
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI
  DATABASE_URL (для хранения токенов)
"""

import json
import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# ── Конфигурация ──────────────────────────────────────────────────────────────
CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
REDIRECT_URI  = os.getenv("YOUTUBE_REDIRECT_URI", "").strip()

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    # youtube.upload покрывает insert/delete/thumbnails.set, но НЕ videos.update —
    # для правки названия/описания уже опубликованного видео нужен этот scope.
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

_TOKEN_DB_KEY = "youtube_oauth"
_TOKEN_URL    = "https://oauth2.googleapis.com/token"
_API_BASE     = "https://www.googleapis.com/youtube/v3"


# ── Хранение токенов в БД ─────────────────────────────────────────────────────

def _save_tokens(tokens: dict):
    from data.database import get_conn
    data = json.dumps(tokens).encode()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bot_config (key, data, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
                """,
                (_TOKEN_DB_KEY, data),
            )
        conn.commit()


def _load_tokens() -> dict | None:
    from data.database import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM bot_config WHERE key=%s", (_TOKEN_DB_KEY,))
            row = cur.fetchone()
    if not row or not row[0]:
        return None
    return json.loads(bytes(row[0]).decode())


# ── OAuth2 ────────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def get_auth_url() -> str:
    if not is_configured():
        raise RuntimeError("YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REDIRECT_URI не заданы")
    scope = " ".join(_SCOPES)
    params = (
        f"client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope.replace(' ', '%20')}"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def is_authorized() -> bool:
    tokens = _load_tokens()
    return tokens is not None and bool(tokens.get("refresh_token"))


def exchange_code(code: str) -> dict:
    """Обменивает authorization code на access/refresh токены и сохраняет в DB."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.post(_TOKEN_URL, data={
            "code":          code,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        })
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)
    return tokens


def _get_access_token() -> str:
    tokens = _load_tokens()
    if not tokens:
        raise RuntimeError("YouTube не авторизован. Запусти /youtube_auth")

    # Если access_token ещё актуален — возвращаем сразу
    expires_at = tokens.get("expires_at", 0)
    if expires_at and datetime.now(timezone.utc).timestamp() < expires_at - 60:
        return tokens["access_token"]

    # Обновляем через refresh_token
    with httpx.Client(timeout=15) as cl:
        resp = cl.post(_TOKEN_URL, data={
            "refresh_token": tokens["refresh_token"],
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type":    "refresh_token",
        })
    resp.raise_for_status()
    new_tokens = resp.json()
    tokens["access_token"] = new_tokens["access_token"]
    tokens["expires_at"]   = datetime.now(timezone.utc).timestamp() + new_tokens.get("expires_in", 3600)
    if "refresh_token" in new_tokens:
        tokens["refresh_token"] = new_tokens["refresh_token"]
    _save_tokens(tokens)
    return tokens["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_access_token()}"}


# ── Канал ─────────────────────────────────────────────────────────────────────

def get_channel_info() -> dict:
    """Возвращает основную информацию о канале (название, подписчики, видео, просмотры)."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.get(
            f"{_API_BASE}/channels",
            params={"part": "snippet,statistics", "mine": "true"},
            headers=_headers(),
        )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return {}
    item = items[0]
    stats = item.get("statistics", {})
    return {
        "id":          item["id"],
        "title":       item["snippet"]["title"],
        "description": item["snippet"]["description"],
        "subscribers": int(stats.get("subscriberCount", 0)),
        "views":       int(stats.get("viewCount", 0)),
        "videos":      int(stats.get("videoCount", 0)),
    }


# ── Видео ─────────────────────────────────────────────────────────────────────

def upload_video(
    file_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = "26",   # 26 = How-to & Style; 17 = Sports; 22 = People & Blogs
    privacy: str = "public",   # public | unlisted | private
    shorts: bool = False,
) -> str:
    """
    Загружает видео на YouTube. Возвращает YouTube video ID.
    Для Shorts добавляет #Shorts в заголовок автоматически.
    """
    if shorts and "#Shorts" not in title:
        title = f"{title} #Shorts"

    metadata = {
        "snippet": {
            "title":       title[:100],
            "description": description[:5000],
            "tags":        tags[:500],
            "categoryId":  category_id,
        },
        "status": {"privacyStatus": privacy},
    }

    file_size = os.path.getsize(file_path)
    access_token = _get_access_token()

    # Resumable upload
    with httpx.Client(timeout=60) as cl:
        init_resp = cl.post(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status",
            headers={
                "Authorization":   f"Bearer {access_token}",
                "Content-Type":    "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(file_size),
            },
            content=json.dumps(metadata).encode(),
        )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    # Загружаем файл по частям (chunk 10 MB)
    chunk_size = 10 * 1024 * 1024
    uploaded   = 0
    video_id   = None

    with open(file_path, "rb") as f:
        while uploaded < file_size:
            chunk = f.read(chunk_size)
            end   = uploaded + len(chunk) - 1
            with httpx.Client(timeout=120) as cl:
                resp = cl.put(
                    upload_url,
                    headers={
                        "Authorization":  f"Bearer {access_token}",
                        "Content-Range":  f"bytes {uploaded}-{end}/{file_size}",
                        "Content-Length": str(len(chunk)),
                    },
                    content=chunk,
                )
            if resp.status_code in (200, 201):
                video_id = resp.json()["id"]
                break
            if resp.status_code == 308:
                uploaded = end + 1
                range_hdr = resp.headers.get("Range", "")
                if range_hdr:
                    uploaded = int(range_hdr.split("-")[1]) + 1
                continue
            resp.raise_for_status()

    if not video_id:
        raise RuntimeError("Загрузка видео завершилась без video_id")

    logger.info(f"Видео загружено: {video_id}")
    return video_id


def get_video_stats(video_id: str) -> dict:
    """Возвращает статистику конкретного видео."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.get(
            f"{_API_BASE}/videos",
            params={"part": "statistics,snippet", "id": video_id},
            headers=_headers(),
        )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return {}
    stats   = items[0]["statistics"]
    snippet = items[0]["snippet"]
    return {
        "id":            video_id,
        "title":         snippet["title"],
        "views":         int(stats.get("viewCount", 0)),
        "likes":         int(stats.get("likeCount", 0)),
        "comments":      int(stats.get("commentCount", 0)),
        "published_at":  snippet.get("publishedAt", ""),
    }


def get_recent_videos(max_results: int = 10) -> list[dict]:
    """Возвращает последние видео канала."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.get(
            f"{_API_BASE}/search",
            params={
                "part":       "snippet",
                "forMine":    "true",
                "type":       "video",
                "order":      "date",
                "maxResults": max_results,
            },
            headers=_headers(),
        )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [
        {
            "id":    item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "date":  item["snippet"]["publishedAt"][:10],
        }
        for item in items
    ]


def list_playlists() -> list[dict]:
    """Плейлисты канала — для поиска уже существующего по названию перед созданием нового."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.get(
            f"{_API_BASE}/playlists",
            params={"part": "snippet", "mine": "true", "maxResults": 50},
            headers=_headers(),
        )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [{"id": it["id"], "title": it["snippet"]["title"]} for it in items]


def create_playlist(title: str, description: str = "", privacy: str = "public") -> str:
    """Создаёт плейлист, возвращает его ID."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.post(
            f"{_API_BASE}/playlists?part=snippet,status",
            headers={
                "Authorization": f"Bearer {_get_access_token()}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
            content=json.dumps({
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": privacy},
            }).encode(),
        )
    resp.raise_for_status()
    return resp.json()["id"]


def get_playlist_item_video_ids(playlist_id: str) -> set[str]:
    """video ID, которые уже есть в плейлисте — чтобы не добавлять дубликаты."""
    ids = set()
    page_token = None
    with httpx.Client(timeout=15) as cl:
        while True:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
            if page_token:
                params["pageToken"] = page_token
            resp = cl.get(f"{_API_BASE}/playlistItems", params=params, headers=_headers())
            resp.raise_for_status()
            data = resp.json()
            ids.update(it["contentDetails"]["videoId"] for it in data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return ids


def add_playlist_item(playlist_id: str, video_id: str):
    """Добавляет видео в конец плейлиста."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.post(
            f"{_API_BASE}/playlistItems?part=snippet",
            headers={
                "Authorization": f"Bearer {_get_access_token()}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
            content=json.dumps({
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                },
            }).encode(),
        )
    resp.raise_for_status()


def get_video_snippet(video_id: str) -> dict:
    """Текущий snippet видео (title, description, tags, categoryId и т.п.) —
    источник истины перед update_video, чтобы не затереть поля, которые не меняем."""
    with httpx.Client(timeout=15) as cl:
        resp = cl.get(
            f"{_API_BASE}/videos",
            params={"part": "snippet", "id": video_id},
            headers=_headers(),
        )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        raise RuntimeError(f"Видео {video_id} не найдено на канале")
    return items[0]["snippet"]


def update_video(
    video_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> None:
    """
    Обновляет метаданные уже опубликованного видео (videos.update).
    YouTube требует передавать snippet целиком, поэтому подгружаем текущий
    и подменяем только переданные поля — остальное (categoryId и т.п.)
    остаётся как было.
    """
    snippet = get_video_snippet(video_id)
    if title is not None:
        snippet["title"] = title[:100]
    if description is not None:
        snippet["description"] = description[:5000]
    if tags is not None:
        snippet["tags"] = tags[:500]

    access_token = _get_access_token()
    with httpx.Client(timeout=30) as cl:
        resp = cl.put(
            f"{_API_BASE}/videos?part=snippet",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
            content=json.dumps({"id": video_id, "snippet": snippet}).encode(),
        )
    resp.raise_for_status()
    logger.info(f"Видео {video_id} обновлено на YouTube")


def set_thumbnail(video_id: str, image_bytes: bytes):
    """Устанавливает кастомную превью-картинку для видео."""
    access_token = _get_access_token()
    with httpx.Client(timeout=30) as cl:
        resp = cl.post(
            f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
            f"?videoId={video_id}&uploadType=media",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "image/jpeg",
            },
            content=image_bytes,
        )
    resp.raise_for_status()
    logger.info(f"Превью установлена для видео {video_id}")
