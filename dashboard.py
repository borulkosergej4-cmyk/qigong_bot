import html as _html
import os
import secrets
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from data.database import get_bookings, get_bookings_stats, get_posts_history, get_scheduled_posts, get_subscription_links

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
    if "vk" in (p or "").lower() and "tg" in (p or "").lower():
        return '<span class="badge-vk">ВКонтакте</span> <span class="badge-tg">Telegram</span>'
    if "vk" in (p or "").lower():
        return '<span class="badge-vk">ВКонтакте</span>'
    return '<span class="badge-tg">Telegram</span>'


PAGE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Цигун и ушу — Дашборд</title>
<style>
  :root {{
    --green:      #2c5f2e;
    --green-light:#e8f5e9;
    --green-mid:  #4a8c4d;
    --bg:         #f4f7f4;
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
  h1, h2 {{ font-family: Georgia, serif; }}
  .header-bar {{
    background: linear-gradient(135deg, #f0f7f0 0%, #e8f5e9 100%);
    border-bottom: 1px solid var(--border);
    padding: 18px 24px;
    display: flex; align-items: center; gap: 14px;
  }}
  .card {{
    background: #fff;
    border-radius: 14px;
    border: 1px solid var(--border);
    box-shadow: 0 2px 10px rgba(44,95,46,0.06);
    overflow: hidden;
    margin-bottom: 20px;
  }}
  .card-header {{
    padding: 12px 18px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(to right, #f4f9f4, #edf5ed);
    display: flex; align-items: center; gap: 8px;
  }}
  .card-header h2 {{ font-size: 15px; font-weight: 600; color: var(--text); }}
  .count-bubble {{
    margin-left: auto;
    background: var(--green);
    color: #fff;
    font-size: 11px; font-weight: 700;
    min-width: 20px; height: 20px;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    padding: 0 6px;
  }}
  .stat-card {{
    background: #fff;
    border-radius: 12px;
    border: 1px solid var(--border);
    padding: 14px 16px;
    box-shadow: 0 1px 6px rgba(44,95,46,0.05);
  }}
  .stat-label {{ font-size: 11px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text-muted); }}
  .stat-value {{ font-family: Georgia, serif; font-size: 30px; font-weight: 700; margin-top: 4px; color: var(--green); }}
  .stat-value.today {{ color: #16a34a; }}
  .stat-value.tg    {{ color: #0369a1; }}
  .stat-value.vk    {{ color: #4338ca; }}
  .row-item {{
    padding: 11px 18px;
    border-bottom: 1px solid #f0f5f0;
    display: flex; align-items: flex-start; gap: 10px;
  }}
  .row-item:last-child {{ border-bottom: none; }}
  .row-item:hover {{ background: #f8fbf8; }}
  .row-text {{ font-size: 13.5px; color: #1f1f1f; line-height: 1.45; }}
  .row-meta {{ font-size: 12px; color: var(--text-muted); margin-top: 2px; }}
  .row-time {{ font-size: 11.5px; color: var(--text-muted); white-space: nowrap; flex-shrink: 0; }}
  .badge-tg, .badge-vk, .badge-other, .badge-pub, .badge-sched, .badge-wait {{
    display: inline-block; font-size: 11px; font-weight: 600;
    padding: 2px 7px; border-radius: 20px; white-space: nowrap;
  }}
  .badge-tg    {{ background: #e0f2fe; color: #0369a1; }}
  .badge-vk    {{ background: #e0e7ff; color: #3730a3; }}
  .badge-other {{ background: #f3f4f6; color: #6b7280; }}
  .badge-pub   {{ background: #dcfce7; color: #15803d; }}
  .badge-sched {{ background: #fff7ed; color: #c2410c; }}
  .badge-wait  {{ background: #f3f4f6; color: #6b7280; }}
  .empty-state {{ padding: 28px 18px; text-align: center; color: var(--text-muted); font-size: 13px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }}
  .grid-4 {{ display: grid; grid-template-columns: repeat(2,1fr); gap: 12px; margin-bottom: 24px; }}
  @media(min-width: 640px) {{ .grid-4 {{ grid-template-columns: repeat(4,1fr); }} }}
  @media(min-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>

<div class="header-bar">
  <img src="/static/logo.png" alt="Цигун" style="height:48px;width:auto;" onerror="this.style.display='none'">
  <div>
    <h1 style="font-size:20px;font-weight:700;color:var(--text);">🌿 Оздоровительный цигун и ушу</h1>
    <p style="font-size:12px;color:var(--text-muted);margin-top:3px;">
      Обновлено {now_msk} МСК &nbsp;·&nbsp; автообновление каждые 60 с
    </p>
  </div>
</div>

<div style="max-width:1080px;margin:0 auto;padding:20px;">

  <!-- Статистика заявок -->
  <div class="grid-4">
    <div class="stat-card">
      <p class="stat-label">Всего заявок</p>
      <p class="stat-value">{stat_total}</p>
    </div>
    <div class="stat-card">
      <p class="stat-label">Сегодня</p>
      <p class="stat-value today">{stat_today}</p>
    </div>
    <div class="stat-card">
      <p class="stat-label">Telegram</p>
      <p class="stat-value tg">{stat_tg}</p>
    </div>
    <div class="stat-card">
      <p class="stat-label">ВКонтакте</p>
      <p class="stat-value vk">{stat_vk}</p>
    </div>
  </div>

  <div class="grid-2">

    <!-- Заявки -->
    <div class="card">
      <div class="card-header">
        <span>👤</span>
        <h2>Последние заявки</h2>
        <div class="count-bubble">{stat_total}</div>
      </div>
      {bookings_html}
    </div>

    <!-- Запланированные посты -->
    <div class="card">
      <div class="card-header">
        <span>⏰</span>
        <h2>Запланированные посты</h2>
        <div class="count-bubble">{scheduled_count}</div>
      </div>
      {scheduled_html}
    </div>

  </div>

  <!-- История постов -->
  <div class="card">
    <div class="card-header">
      <span>📋</span>
      <h2>История постов</h2>
      <div class="count-bubble">{history_count}</div>
    </div>
    {history_html}
  </div>

  <!-- Подписные ссылки -->
  <div class="card">
    <div class="card-header">
      <span>🔗</span>
      <h2>Подписные площадки</h2>
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
    from datetime import datetime
    try:
        scheduled = get_scheduled_posts()
        history = get_posts_history(50)
        links = get_subscription_links()
        bookings = get_bookings(30)
        stats = get_bookings_stats()
    except Exception as ex:
        return HTMLResponse(f"<h1>Ошибка БД: {e(str(ex))}</h1>", status_code=500)

    now_msk = (datetime.utcnow() + MOSCOW).strftime("%d.%m.%Y %H:%M")

    # Заявки
    if bookings:
        rows = ""
        for row in bookings:
            bid, name, phone, source, username, created_at = row
            rows += f"""
<div class="row-item">
  <div style="flex:1">
    <div class="row-text"><b>{e(name)}</b> &nbsp; {e(phone)}</div>
    <div class="row-meta">
      {source_badge(source or "")}
      {"&nbsp; @" + e(username) if username else ""}
    </div>
  </div>
  <div class="row-time">{msk(created_at)}</div>
</div>"""
        bookings_html = rows
    else:
        bookings_html = '<div class="empty-state">Заявок пока нет</div>'

    # Запланированные посты
    if scheduled:
        rows = ""
        for row in scheduled:
            pid, platform, text, _, sched_at = row
            rows += f"""
<div class="row-item">
  <div style="flex:1">
    <div class="row-text" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{e(text[:300])}</div>
    <div class="row-meta">{platform_badge(platform)}</div>
  </div>
  <div class="row-time">{msk(sched_at)}</div>
</div>"""
        scheduled_html = rows
    else:
        scheduled_html = '<div class="empty-state">Нет запланированных постов</div>'

    # История постов
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
    <div class="row-text" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{e(text[:300])}</div>
    <div class="row-meta">{platform_badge(platform)} &nbsp; {badge}</div>
  </div>
  <div class="row-time">{msk(created_at)}</div>
</div>"""
        history_html = rows
    else:
        history_html = '<div class="empty-state">Постов пока нет</div>'

    # Подписные ссылки
    if links:
        rows = ""
        for row in links:
            lid, name, url, desc = row
            rows += f"""
<div class="row-item">
  <div style="flex:1">
    <div class="row-text"><a href="{e(url)}" target="_blank" style="color:var(--green);text-decoration:none">{e(name)}</a></div>
    <div class="row-meta">{e(desc) if desc else e(url)}</div>
  </div>
</div>"""
        links_html = rows
    else:
        links_html = '<div class="empty-state">Ссылки не добавлены</div>'

    html = PAGE.format(
        now_msk=now_msk,
        stat_total=stats["total"],
        stat_today=stats["today"],
        stat_tg=stats["telegram"],
        stat_vk=stats["vk"],
        bookings_html=bookings_html,
        scheduled_html=scheduled_html,
        scheduled_count=len(scheduled),
        history_html=history_html,
        history_count=len(history),
        links_html=links_html,
        links_count=len(links),
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
