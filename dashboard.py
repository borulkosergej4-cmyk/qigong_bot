import html as _html
import json
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from data.database import (
    get_bookings, get_bookings_stats, get_posts_history, get_scheduled_posts,
    get_subscription_links, get_content_plan, get_bg_videos, get_bg_audios, get_logo,
)

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "qigong")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
MOSCOW = timedelta(hours=3)

app = FastAPI(docs_url=None, redoc_url=None)
security = HTTPBasic()

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


def require_auth(creds: HTTPBasicCredentials = Depends(security)):
    user_ok = secrets.compare_digest(creds.username.encode(), DASHBOARD_USERNAME.encode())
    pass_ok = secrets.compare_digest(creds.password.encode(), DASHBOARD_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


def msk(dt) -> str:
    if dt is None:
        return "—"
    return (dt + MOSCOW).strftime("%d.%m %H:%M")


def e(s) -> str:
    return _html.escape(str(s or ""))


def source_badge(source: str) -> str:
    if source == "telegram":
        return '<span class="badge-tg">Telegram</span>'
    if source == "vk":
        return '<span class="badge-vk">ВКонтакте</span>'
    return '<span class="badge-other">—</span>'


def platform_badge(p: str) -> str:
    p = (p or "").lower()
    if "vk" in p and "tg" in p:
        return '<span class="badge-vk">ВКонтакте</span> <span class="badge-tg">Telegram</span>'
    if "vk" in p:
        return '<span class="badge-vk">ВКонтакте</span>'
    return '<span class="badge-tg">Telegram</span>'


PAGE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Цигун и ушу — Панель управления</title>
<style>
  :root {{
    --green:      #2c5f2e;
    --green-light:#e8f5e9;
    --green-mid:  #4a8c4d;
    --bg:         #f0f4f0;
    --border:     #c8dfc9;
    --text:       #1a3a1c;
    --text-muted: #5a7a5c;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: #1f1f1f;
    min-height: 100vh;
  }}
  h1, h2, h3 {{ font-family: Georgia, serif; }}

  /* Шапка */
  .header-bar {{
    background: linear-gradient(135deg, #1a3a1c 0%, #2c5f2e 60%, #4a8c4d 100%);
    padding: 20px 28px;
    display: flex; align-items: center; gap: 16px;
    box-shadow: 0 3px 12px rgba(0,0,0,0.18);
  }}
  .header-bar h1 {{ font-size: 21px; font-weight: 700; color: #e8f5e9; letter-spacing: 0.01em; }}
  .header-sub {{ font-size: 12px; color: #a5c8a7; margin-top: 4px; }}

  /* Секции */
  .section-label {{
    font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--text-muted);
    margin: 24px 0 10px;
  }}

  /* Карточки */
  .card {{
    background: #fff;
    border-radius: 14px;
    border: 1px solid var(--border);
    box-shadow: 0 2px 10px rgba(44,95,46,0.06);
    overflow: hidden;
    margin-bottom: 16px;
  }}
  .card-header {{
    padding: 12px 18px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(to right, #f4f9f4, #edf5ed);
    display: flex; align-items: center; gap: 8px;
  }}
  .card-header h2 {{ font-size: 14px; font-weight: 700; color: var(--text); }}
  .count-bubble {{
    margin-left: auto;
    background: var(--green);
    color: #fff;
    font-size: 11px; font-weight: 700;
    min-width: 22px; height: 22px;
    border-radius: 11px;
    display: flex; align-items: center; justify-content: center;
    padding: 0 7px;
  }}

  /* Статистика */
  .stat-card {{
    background: #fff;
    border-radius: 12px;
    border: 1px solid var(--border);
    padding: 16px 18px;
    box-shadow: 0 1px 6px rgba(44,95,46,0.05);
    display: flex; flex-direction: column; gap: 4px;
  }}
  .stat-label {{ font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }}
  .stat-value {{ font-family: Georgia, serif; font-size: 32px; font-weight: 700; color: var(--green); line-height: 1; }}
  .stat-value.today {{ color: #16a34a; }}
  .stat-value.tg    {{ color: #0369a1; }}
  .stat-value.vk    {{ color: #4338ca; }}
  .stat-hint {{ font-size: 11px; color: var(--text-muted); }}

  /* Строки */
  .row-item {{
    padding: 11px 18px;
    border-bottom: 1px solid #f0f5f0;
    display: flex; align-items: flex-start; gap: 10px;
  }}
  .row-item:last-child {{ border-bottom: none; }}
  .row-item:hover {{ background: #f8fbf8; }}
  .row-text {{ font-size: 13.5px; color: #1f1f1f; line-height: 1.45; }}
  .row-meta {{ font-size: 12px; color: var(--text-muted); margin-top: 3px; }}
  .row-time {{ font-size: 11.5px; color: var(--text-muted); white-space: nowrap; flex-shrink: 0; }}

  /* Бейджи */
  .badge-tg, .badge-vk, .badge-other,
  .badge-pub, .badge-sched, .badge-wait,
  .badge-ok, .badge-warn, .badge-cp-ok, .badge-cp-draft {{
    display: inline-block; font-size: 11px; font-weight: 600;
    padding: 2px 8px; border-radius: 20px; white-space: nowrap;
  }}
  .badge-tg      {{ background: #e0f2fe; color: #0369a1; }}
  .badge-vk      {{ background: #e0e7ff; color: #3730a3; }}
  .badge-other   {{ background: #f3f4f6; color: #6b7280; }}
  .badge-pub     {{ background: #dcfce7; color: #15803d; }}
  .badge-sched   {{ background: #fff7ed; color: #c2410c; }}
  .badge-wait    {{ background: #f3f4f6; color: #6b7280; }}
  .badge-ok      {{ background: #dcfce7; color: #15803d; }}
  .badge-warn    {{ background: #fef9c3; color: #92400e; }}
  .badge-cp-ok   {{ background: #dcfce7; color: #15803d; }}
  .badge-cp-draft{{ background: #fff7ed; color: #c2410c; }}

  /* Статус-панель */
  .status-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    padding: 14px 18px;
  }}
  @media(min-width: 640px) {{
    .status-grid {{ grid-template-columns: repeat(4, 1fr); }}
  }}
  .status-item {{ display: flex; flex-direction: column; gap: 4px; }}
  .status-name {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }}
  .status-val  {{ font-size: 13px; font-weight: 600; color: var(--text); }}

  /* Контент-план */
  .cp-item {{
    display: flex; align-items: flex-start; gap: 10px;
    padding: 9px 18px;
    border-bottom: 1px solid #f0f5f0;
    font-size: 13px;
  }}
  .cp-item:last-child {{ border-bottom: none; }}
  .cp-day {{ min-width: 28px; font-weight: 700; color: var(--green); font-family: Georgia, serif; }}
  .cp-topic {{ color: #1f1f1f; flex: 1; }}
  .cp-fmt {{ font-size: 11px; color: var(--text-muted); }}

  /* Медиа */
  .media-grid {{
    display: grid; grid-template-columns: repeat(3,1fr); gap: 10px;
    padding: 14px 18px;
  }}
  .media-tile {{
    border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 14px;
    background: #fafcfa;
    display: flex; flex-direction: column; gap: 5px;
  }}
  .media-icon {{ font-size: 22px; }}
  .media-name {{ font-size: 12px; font-weight: 700; color: var(--text); }}
  .media-count {{ font-size: 11px; color: var(--text-muted); }}

  /* Гриды */
  .grid-4 {{ display: grid; grid-template-columns: repeat(2,1fr); gap: 12px; margin-bottom: 16px; }}
  @media(min-width: 640px) {{ .grid-4 {{ grid-template-columns: repeat(4,1fr); }} }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
  @media(min-width: 820px) {{ .grid-2 {{ grid-template-columns: 1fr 1fr; }} }}

  .empty-state {{ padding: 28px 18px; text-align: center; color: var(--text-muted); font-size: 13px; }}
  a {{ color: var(--green); }}
</style>
</head>
<body>

<!-- Шапка -->
<div class="header-bar">
  <img src="/static/logo.png" alt="" style="height:52px;width:auto;border-radius:8px;"
       onerror="this.style.display='none'">
  <div>
    <h1>🌿 Целительный цигун и ушу</h1>
    <p class="header-sub">Панель управления · {now_msk} МСК · автообновление 60 с</p>
  </div>
</div>

<div style="max-width:1100px;margin:0 auto;padding:20px 16px;">

  <!-- Статус системы -->
  <p class="section-label">⚙️ Статус системы</p>
  <div class="card" style="margin-bottom:16px;">
    <div class="status-grid">
      <div class="status-item">
        <span class="status-name">Бот</span>
        <span class="status-val">{bot_status}</span>
      </div>
      <div class="status-item">
        <span class="status-name">Режим</span>
        <span class="status-val">{bot_mode}</span>
      </div>
      <div class="status-item">
        <span class="status-name">Домен</span>
        <span class="status-val" style="font-size:11px;word-break:break-all">{railway_domain}</span>
      </div>
      <div class="status-item">
        <span class="status-name">Версия</span>
        <span class="status-val">v4 · 2026-06-29</span>
      </div>
    </div>
  </div>

  <!-- Статистика заявок -->
  <p class="section-label">📊 Статистика заявок</p>
  <div class="grid-4">
    <div class="stat-card">
      <span class="stat-label">Всего</span>
      <span class="stat-value">{stat_total}</span>
      <span class="stat-hint">за всё время</span>
    </div>
    <div class="stat-card">
      <span class="stat-label">Сегодня</span>
      <span class="stat-value today">{stat_today}</span>
      <span class="stat-hint">новых заявок</span>
    </div>
    <div class="stat-card">
      <span class="stat-label">Telegram</span>
      <span class="stat-value tg">{stat_tg}</span>
      <span class="stat-hint">из Telegram</span>
    </div>
    <div class="stat-card">
      <span class="stat-label">ВКонтакте</span>
      <span class="stat-value vk">{stat_vk}</span>
      <span class="stat-hint">из ВКонтакте</span>
    </div>
  </div>

  <!-- Основная сетка -->
  <div class="grid-2">

    <!-- Заявки -->
    <div>
      <p class="section-label">👤 Последние заявки</p>
      <div class="card">
        <div class="card-header">
          <span>👤</span><h2>Заявки</h2>
          <div class="count-bubble">{stat_total}</div>
        </div>
        {bookings_html}
      </div>
    </div>

    <!-- Посты -->
    <div>
      <p class="section-label">⏰ Запланированные посты</p>
      <div class="card">
        <div class="card-header">
          <span>⏰</span><h2>Запланированные</h2>
          <div class="count-bubble">{scheduled_count}</div>
        </div>
        {scheduled_html}
      </div>
    </div>

  </div>

  <!-- Контент-план -->
  <p class="section-label">📅 Контент-план · {cp_month}</p>
  <div class="card" style="margin-bottom:16px;">
    <div class="card-header">
      <span>📅</span><h2>Контент-план</h2>
      <div style="margin-left:auto">{cp_status_badge}</div>
    </div>
    {cp_html}
  </div>

  <!-- Медиафайлы -->
  <p class="section-label">🎬 Медиафайлы</p>
  <div class="card" style="margin-bottom:16px;">
    <div class="card-header"><span>🎬</span><h2>Медиафайлы</h2></div>
    <div class="media-grid">
      <div class="media-tile">
        <span class="media-icon">🎥</span>
        <span class="media-name">Видео-фоны</span>
        <span class="media-count">{videos_count} файлов</span>
      </div>
      <div class="media-tile">
        <span class="media-icon">🎵</span>
        <span class="media-name">Аудио-треки</span>
        <span class="media-count">{audios_count} файлов</span>
      </div>
      <div class="media-tile">
        <span class="media-icon">🖼</span>
        <span class="media-name">Логотип</span>
        <span class="media-count">{logo_status}</span>
      </div>
    </div>
    {media_names_html}
  </div>

  <!-- История постов -->
  <p class="section-label">📋 История постов</p>
  <div class="card" style="margin-bottom:16px;">
    <div class="card-header">
      <span>📋</span><h2>История постов</h2>
      <div class="count-bubble">{history_count}</div>
    </div>
    {history_html}
  </div>

  <!-- Подписные ссылки -->
  <p class="section-label">🔗 Подписные площадки</p>
  <div class="card">
    <div class="card-header">
      <span>🔗</span><h2>Площадки</h2>
      <div class="count-bubble">{links_count}</div>
    </div>
    {links_html}
  </div>

</div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard(username: str = Depends(require_auth)):
    try:
        scheduled = get_scheduled_posts()
        history   = get_posts_history(30)
        links     = get_subscription_links()
        bookings  = get_bookings(25)
        stats     = get_bookings_stats()
        videos    = get_bg_videos()
        audios    = get_bg_audios()
        logo_data = get_logo()
        cp_month  = datetime.utcnow().strftime("%Y-%m")
        cp_row    = get_content_plan(cp_month)
    except Exception as ex:
        return HTMLResponse(f"<h1>Ошибка БД: {e(str(ex))}</h1>", status_code=500)

    now_msk = (datetime.utcnow() + MOSCOW).strftime("%d.%m.%Y %H:%M")

    # ── Статус системы ──────────────────────────────────────────────────────
    import app_state
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "—")
    if app_state.admin_application:
        bot_status = '<span class="badge-ok">✅ работает</span>'
        bot_mode   = "webhook" if railway_domain != "—" else "polling"
    else:
        bot_status = '<span class="badge-warn">⚠ не запущен</span>'
        bot_mode   = "—"

    # ── Заявки ──────────────────────────────────────────────────────────────
    if bookings:
        rows = ""
        for row in bookings:
            bid, name, phone, source, uname, created_at = row
            rows += f"""
<div class="row-item">
  <div style="flex:1">
    <div class="row-text"><b>{e(name)}</b> &nbsp; {e(phone)}</div>
    <div class="row-meta">{source_badge(source or "")}{"&nbsp; @"+e(uname) if uname else ""}</div>
  </div>
  <div class="row-time">{msk(created_at)}</div>
</div>"""
        bookings_html = rows
    else:
        bookings_html = '<div class="empty-state">Заявок пока нет</div>'

    # ── Запланированные посты ───────────────────────────────────────────────
    if scheduled:
        rows = ""
        for row in scheduled:
            pid, platform, text, _, sched_at = row
            rows += f"""
<div class="row-item">
  <div style="flex:1">
    <div class="row-text" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{e((text or "")[:250])}</div>
    <div class="row-meta">{platform_badge(platform)}</div>
  </div>
  <div class="row-time">{msk(sched_at)}</div>
</div>"""
        scheduled_html = rows
    else:
        scheduled_html = '<div class="empty-state">Нет запланированных постов</div>'

    # ── Контент-план ────────────────────────────────────────────────────────
    cp_month_ru = (datetime.utcnow() + MOSCOW).strftime("%B %Y")
    if cp_row:
        cp_id, cp_items_raw, cp_approved = cp_row
        try:
            cp_items = json.loads(cp_items_raw) if isinstance(cp_items_raw, str) else cp_items_raw
        except Exception:
            cp_items = []
        cp_status_badge = ('<span class="badge-cp-ok">✅ утверждён</span>'
                           if cp_approved else
                           '<span class="badge-cp-draft">⏳ черновик</span>')
        if cp_items:
            rows = ""
            for item in cp_items[:20]:
                day   = item.get("day", "")
                topic = item.get("topic", "")
                fmt   = item.get("format", "")
                rows += f"""
<div class="cp-item">
  <span class="cp-day">{e(str(day))}</span>
  <span class="cp-topic">{e(topic)}</span>
  <span class="cp-fmt">{e(fmt)}</span>
</div>"""
            cp_html = rows
        else:
            cp_html = '<div class="empty-state">Элементы плана не найдены</div>'
    else:
        cp_status_badge = '<span class="badge-warn">нет плана</span>'
        cp_html = '<div class="empty-state">Контент-план на этот месяц не создан</div>'

    # ── Медиафайлы ──────────────────────────────────────────────────────────
    videos_count = len(videos)
    audios_count = len(audios)
    logo_status  = "загружен ✅" if logo_data else "не загружен"
    media_rows = ""
    if videos:
        names = ", ".join(r[1] for r in videos[:6])
        if len(videos) > 6:
            names += f" +{len(videos)-6}"
        media_rows += f'<div style="padding:6px 18px 10px;font-size:12px;color:var(--text-muted)">🎥 {e(names)}</div>'
    if audios:
        names = ", ".join(r[1] for r in audios[:6])
        if len(audios) > 6:
            names += f" +{len(audios)-6}"
        media_rows += f'<div style="padding:0 18px 10px;font-size:12px;color:var(--text-muted)">🎵 {e(names)}</div>'
    media_names_html = media_rows

    # ── История постов ──────────────────────────────────────────────────────
    if history:
        rows = ""
        for row in history:
            pid, platform, text, published, sched_at, created_at = row
            if published:
                badge = '<span class="badge-pub">✅ опубликован</span>'
            elif sched_at:
                badge = '<span class="badge-sched">⏳ запланирован</span>'
            else:
                badge = '<span class="badge-wait">черновик</span>'
            rows += f"""
<div class="row-item">
  <div style="flex:1">
    <div class="row-text" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{e((text or "")[:250])}</div>
    <div class="row-meta">{platform_badge(platform)} &nbsp; {badge}</div>
  </div>
  <div class="row-time">{msk(created_at)}</div>
</div>"""
        history_html = rows
    else:
        history_html = '<div class="empty-state">Постов пока нет</div>'

    # ── Подписные ссылки ────────────────────────────────────────────────────
    if links:
        rows = ""
        for row in links:
            lid, name, url, desc = row
            rows += f"""
<div class="row-item">
  <div style="flex:1">
    <div class="row-text"><a href="{e(url)}" target="_blank">{e(name)}</a></div>
    <div class="row-meta">{e(desc) if desc else e(url)}</div>
  </div>
</div>"""
        links_html = rows
    else:
        links_html = '<div class="empty-state">Ссылки не добавлены</div>'

    html = PAGE.format(
        now_msk        = now_msk,
        bot_status     = bot_status,
        bot_mode       = bot_mode,
        railway_domain = railway_domain,
        stat_total     = stats["total"],
        stat_today     = stats["today"],
        stat_tg        = stats["telegram"],
        stat_vk        = stats["vk"],
        bookings_html  = bookings_html,
        scheduled_html = scheduled_html,
        scheduled_count= len(scheduled),
        cp_month       = cp_month_ru,
        cp_status_badge= cp_status_badge,
        cp_html        = cp_html,
        videos_count   = videos_count,
        audios_count   = audios_count,
        logo_status    = logo_status,
        media_names_html= media_names_html,
        history_html   = history_html,
        history_count  = len(history),
        links_html     = links_html,
        links_count    = len(links),
    )
    return HTMLResponse(html)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/admin_bot")
async def admin_bot_webhook(request: Request):
    import app_state
    from telegram import Update

    if not app_state.admin_application:
        return {"ok": False, "error": "bot not ready"}

    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if app_state.webhook_secret and secret != app_state.webhook_secret:
        raise HTTPException(status_code=403)

    data = await request.json()
    update = Update.de_json(data, app_state.admin_application.bot)
    await app_state.admin_application.process_update(update)
    return {"ok": True}
