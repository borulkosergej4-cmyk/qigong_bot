import os
import secrets
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from data.database import get_posts_history, get_scheduled_posts, get_subscription_links

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "qigong")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")

app = FastAPI()
security = HTTPBasic()

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


def require_auth(creds: HTTPBasicCredentials = Depends(security)):
    user_ok = secrets.compare_digest(creds.username.encode(), DASHBOARD_USERNAME.encode())
    pass_ok = secrets.compare_digest(creds.password.encode(), DASHBOARD_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


@app.get("/", response_class=HTMLResponse)
def dashboard(username: str = Depends(require_auth)):
    try:
        scheduled = get_scheduled_posts()
        history = get_posts_history(50)
        links = get_subscription_links()
    except Exception as e:
        return HTMLResponse(f"<h1>Ошибка БД: {e}</h1>", status_code=500)

    def fmt_time(dt):
        if not dt:
            return "—"
        msk = dt + timedelta(hours=3)
        return msk.strftime("%d.%m.%Y %H:%M")

    sched_rows = ""
    for row in scheduled:
        pid, platform, text, _, sched_at = row
        sched_rows += (
            f"<tr><td>{pid}</td><td>{platform}</td>"
            f"<td>{fmt_time(sched_at)}</td>"
            f"<td style='max-width:400px;white-space:pre-wrap'>{text[:200]}</td></tr>"
        )

    hist_rows = ""
    for row in history:
        pid, platform, text, published, sched_at, created_at = row
        status = "✅ опубликован" if published else "⏳ ожидает"
        hist_rows += (
            f"<tr><td>{pid}</td><td>{platform}</td><td>{status}</td>"
            f"<td>{fmt_time(created_at)}</td>"
            f"<td style='max-width:400px;white-space:pre-wrap'>{text[:200]}</td></tr>"
        )

    link_rows = ""
    for row in links:
        lid, name, url, desc = row
        link_rows += (
            f"<tr><td>{lid}</td><td>{name}</td>"
            f"<td><a href='{url}' target='_blank'>{url}</a></td>"
            f"<td>{desc or '—'}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Цигун и ушу — дашборд</title>
<style>
  body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
  h1 {{ color: #2c5f2e; }}
  h2 {{ color: #4a4a4a; margin-top: 30px; }}
  table {{ border-collapse: collapse; width: 100%; background: white; margin-bottom: 20px; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; }}
  th {{ background: #2c5f2e; color: white; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
</style>
</head>
<body>
<h1>🌿 Целительный цигун и ушу</h1>

<h2>⏰ Запланированные посты ({len(scheduled)})</h2>
<table>
<tr><th>ID</th><th>Платформа</th><th>Время (МСК)</th><th>Текст</th></tr>
{sched_rows or '<tr><td colspan="4">Нет запланированных постов</td></tr>'}
</table>

<h2>🔗 Подписные площадки ({len(links)})</h2>
<table>
<tr><th>ID</th><th>Название</th><th>Ссылка</th><th>Описание</th></tr>
{link_rows or '<tr><td colspan="4">Нет добавленных ссылок</td></tr>'}
</table>

<h2>📋 История постов ({len(history)})</h2>
<table>
<tr><th>ID</th><th>Платформа</th><th>Статус</th><th>Создан</th><th>Текст</th></tr>
{hist_rows or '<tr><td colspan="5">Нет постов</td></tr>'}
</table>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/health")
def health():
    return {"status": "ok"}
