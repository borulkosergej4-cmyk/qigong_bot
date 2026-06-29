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
    get_youtube_videos, init_db,
)

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "qigong")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
MOSCOW = timedelta(hours=3)

app = FastAPI(docs_url=None, redoc_url=None)
security = HTTPBasic()

try:
    init_db()
except Exception:
    pass

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


_BADGE_STYLE = 'style="display:inline-flex;align-items:center;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;white-space:nowrap"'


def source_badge(source: str) -> str:
    if source == "telegram":
        return f'<span class="badge-tg" {_BADGE_STYLE}>Telegram</span>'
    if source == "vk":
        return f'<span class="badge-vk" {_BADGE_STYLE}>ВКонтакте</span>'
    return f'<span class="badge-other" {_BADGE_STYLE}>—</span>'


def platform_badge(p: str) -> str:
    p = (p or "").lower()
    if "vk" in p and "tg" in p:
        return (f'<span class="badge-vk" {_BADGE_STYLE}>ВКонтакте</span> '
                f'<span class="badge-tg" {_BADGE_STYLE}>Telegram</span>')
    if "vk" in p:
        return f'<span class="badge-vk" {_BADGE_STYLE}>ВКонтакте</span>'
    return f'<span class="badge-tg" {_BADGE_STYLE}>Telegram</span>'


PAGE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Оздоровительный цигун — Панель управления</title>
<style>
  :root {{
    --bg:         #07090e;
    --surface:    #0d1117;
    --surface-2:  #111827;
    --surface-3:  #1a2332;
    --border:     #1c2536;
    --border-2:   #243048;
    --teal:       #0d9488;
    --teal-2:     #14b8a6;
    --teal-3:     #5eead4;
    --gold:       #d97706;
    --gold-2:     #f59e0b;
    --red:        #ef4444;
    --red-2:      #FF0000;
    --blue:       #3b82f6;
    --blue-2:     #60a5fa;
    --purple:     #8b5cf6;
    --purple-2:   #a78bfa;
    --green:      #10b981;
    --green-2:    #34d399;
    --text:       #e2e8f0;
    --text-2:     #94a3b8;
    --text-3:     #475569;
    --text-4:     #1e293b;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100dvh;
  }}

  /* ── Шапка ──────────────────────────────────────────────────── */
  .header-bar {{
    background: linear-gradient(135deg,
      #040b14 0%, #071526 40%, #091e35 70%, #0b2645 100%);
    border-bottom: 1px solid var(--border-2);
    padding: 0 28px;
    display: flex; align-items: stretch; gap: 0;
    position: sticky; top: 0; z-index: 100;
    box-shadow: 0 1px 0 var(--border), 0 8px 32px rgba(0,0,0,0.4);
  }}
  .header-brand {{
    display: flex; align-items: center; gap: 14px;
    padding: 18px 0; flex: 1;
  }}
  .header-logo {{
    width: 42px; height: 42px; border-radius: 10px;
    object-fit: cover; flex-shrink: 0;
    border: 1px solid var(--border-2);
  }}
  .header-logo-placeholder {{
    width: 42px; height: 42px; border-radius: 10px; flex-shrink: 0;
    background: linear-gradient(135deg, var(--teal) 0%, #065f52 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; border: 1px solid var(--border-2);
  }}
  .header-title {{ font-size: 17px; font-weight: 700; color: var(--text); letter-spacing: -0.01em; }}
  .header-sub   {{ font-size: 11px; color: var(--text-3); margin-top: 3px; }}
  .header-status {{
    display: flex; align-items: center; gap: 6px;
    padding: 18px 0 18px 24px; border-left: 1px solid var(--border);
    margin-left: 16px;
  }}
  .dot-online {{ width: 8px; height: 8px; border-radius: 50%;
                 background: var(--green-2);
                 box-shadow: 0 0 0 2px rgba(52,211,153,0.25); flex-shrink: 0; }}
  .dot-offline {{ width: 8px; height: 8px; border-radius: 50%;
                  background: var(--text-3); flex-shrink: 0; }}
  .header-status-text {{ font-size: 12px; font-weight: 600; color: var(--text-2); }}

  /* ── Основной контейнер ─────────────────────────────────────── */
  .wrap {{ max-width: 1160px; margin: 0 auto; padding: 24px 20px 40px; }}

  /* ── Секция-заголовок ───────────────────────────────────────── */
  .sec-label {{
    font-size: 10px; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; color: var(--text-3);
    margin: 28px 0 12px; display: flex; align-items: center; gap: 8px;
  }}
  .sec-label::after {{
    content: ''; flex: 1; height: 1px; background: var(--border);
  }}

  /* ── KPI-полоса ─────────────────────────────────────────────── */
  .kpi-strip {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px; margin-bottom: 0;
  }}
  @media(min-width:640px) {{ .kpi-strip {{ grid-template-columns: repeat(4, 1fr); }} }}

  .kpi-card {{
    border-radius: 14px;
    border: 1px solid var(--border);
    padding: 18px 20px;
    display: flex; flex-direction: column; gap: 6px;
    position: relative; overflow: hidden;
  }}
  .kpi-card::before {{
    content: ''; position: absolute; inset: 0;
    background: var(--kpi-glow, transparent);
    opacity: 0.07; pointer-events: none;
  }}
  .kpi-card.c-total  {{ background: var(--surface); --kpi-glow: var(--teal); }}
  .kpi-card.c-today  {{ background: var(--surface); --kpi-glow: var(--green); }}
  .kpi-card.c-tg     {{ background: var(--surface); --kpi-glow: var(--blue); }}
  .kpi-card.c-vk     {{ background: var(--surface); --kpi-glow: var(--purple); }}

  .kpi-label {{
    font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--text-3);
  }}
  .kpi-value {{
    font-size: 36px; font-weight: 800; line-height: 1;
    font-variant-numeric: tabular-nums;
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  .kpi-value.v-total {{ background: linear-gradient(135deg, var(--teal-3), var(--teal-2)); }}
  .kpi-value.v-today {{ background: linear-gradient(135deg, var(--green-2), var(--green)); }}
  .kpi-value.v-tg    {{ background: linear-gradient(135deg, var(--blue-2), var(--blue)); }}
  .kpi-value.v-vk    {{ background: linear-gradient(135deg, var(--purple-2), var(--purple)); }}
  .kpi-hint {{ font-size: 11px; color: var(--text-3); }}

  /* ── Карточка ───────────────────────────────────────────────── */
  .card {{
    background: var(--surface);
    border-radius: 14px;
    border: 1px solid var(--border);
    overflow: hidden;
    margin-bottom: 0;
  }}
  .card-head {{
    padding: 13px 18px;
    border-bottom: 1px solid var(--border);
    background: var(--surface-2);
    display: flex; align-items: center; gap: 10px;
  }}
  .card-head-icon {{
    width: 28px; height: 28px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; flex-shrink: 0;
  }}
  .ci-teal   {{ background: rgba(13,148,136,0.18); }}
  .ci-blue   {{ background: rgba(59,130,246,0.18); }}
  .ci-gold   {{ background: rgba(217,119,6,0.18); }}
  .ci-purple {{ background: rgba(139,92,246,0.18); }}
  .ci-red    {{ background: rgba(239,68,68,0.18); }}
  .ci-green  {{ background: rgba(16,185,129,0.18); }}
  .card-head h2 {{ font-size: 13px; font-weight: 700; color: var(--text); }}
  .card-bubble {{
    margin-left: auto;
    background: var(--surface-3);
    border: 1px solid var(--border-2);
    color: var(--text-2);
    font-size: 11px; font-weight: 700;
    min-width: 24px; height: 22px;
    border-radius: 11px;
    display: flex; align-items: center; justify-content: center;
    padding: 0 8px;
  }}

  /* ── Статус системы ─────────────────────────────────────────── */
  .sys-grid {{
    display: grid; grid-template-columns: repeat(2, 1fr); gap: 0;
  }}
  @media(min-width:640px) {{ .sys-grid {{ grid-template-columns: repeat(4, 1fr); }} }}
  .sys-item {{
    padding: 14px 18px;
    border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 5px;
  }}
  .sys-item:nth-child(2n) {{ border-right: none; }}
  @media(min-width:640px) {{
    .sys-item:nth-child(2n) {{ border-right: 1px solid var(--border); }}
    .sys-item:last-child {{ border-right: none; }}
  }}
  .sys-item:nth-last-child(-n+2) {{ border-bottom: none; }}
  @media(min-width:640px) {{
    .sys-item:nth-last-child(-n+4) {{ border-bottom: none; }}
  }}
  .sys-name {{ font-size: 10px; font-weight: 700; text-transform: uppercase;
               letter-spacing: 0.07em; color: var(--text-3); }}
  .sys-val  {{ font-size: 13px; font-weight: 600; color: var(--text-2); word-break: break-all; }}

  /* ── Строки данных ──────────────────────────────────────────── */
  .row-item {{
    padding: 11px 18px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: flex-start; gap: 12px;
    transition: background 0.1s;
  }}
  .row-item:last-child {{ border-bottom: none; }}
  .row-item:hover {{ background: var(--surface-2); }}
  .row-text {{ font-size: 13px; color: var(--text); line-height: 1.5; }}
  .row-meta {{ font-size: 11.5px; color: var(--text-3); margin-top: 3px; display: flex; gap: 6px; align-items: center; }}
  .row-time {{ font-size: 11px; color: var(--text-3); white-space: nowrap; flex-shrink: 0; font-variant-numeric: tabular-nums; }}

  /* ── Бейджи ─────────────────────────────────────────────────── */
  .badge {{
    display: inline-flex; align-items: center;
    font-size: 11px; font-weight: 600;
    padding: 2px 8px; border-radius: 20px; white-space: nowrap;
  }}
  .badge-tg     {{ background: rgba(59,130,246,0.15); color: var(--blue-2); border: 1px solid rgba(59,130,246,0.2); }}
  .badge-vk     {{ background: rgba(139,92,246,0.15); color: var(--purple-2); border: 1px solid rgba(139,92,246,0.2); }}
  .badge-other  {{ background: var(--surface-3); color: var(--text-3); }}
  .badge-pub    {{ background: rgba(16,185,129,0.15); color: var(--green-2); border: 1px solid rgba(16,185,129,0.2); }}
  .badge-sched  {{ background: rgba(217,119,6,0.15); color: var(--gold-2); border: 1px solid rgba(217,119,6,0.2); }}
  .badge-draft  {{ background: var(--surface-3); color: var(--text-2); }}
  .badge-ok     {{ background: rgba(16,185,129,0.15); color: var(--green-2); border: 1px solid rgba(16,185,129,0.2); }}
  .badge-warn   {{ background: rgba(217,119,6,0.15); color: var(--gold-2); border: 1px solid rgba(217,119,6,0.2); }}
  .badge-cp-ok  {{ background: rgba(16,185,129,0.15); color: var(--green-2); border: 1px solid rgba(16,185,129,0.2); }}
  .badge-cp-draft {{ background: rgba(217,119,6,0.15); color: var(--gold-2); border: 1px solid rgba(217,119,6,0.2); }}

  /* ── Контент-план ───────────────────────────────────────────── */
  .cp-item {{
    display: flex; align-items: flex-start; gap: 12px;
    padding: 10px 18px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    transition: background 0.1s;
  }}
  .cp-item:last-child {{ border-bottom: none; }}
  .cp-item:hover {{ background: var(--surface-2); }}
  .cp-day {{
    min-width: 32px; font-weight: 800; font-size: 15px;
    color: var(--teal-2); font-variant-numeric: tabular-nums; flex-shrink: 0;
  }}
  .cp-topic {{ color: var(--text); flex: 1; line-height: 1.4; }}
  .cp-fmt {{
    font-size: 10px; font-weight: 600; color: var(--text-3);
    background: var(--surface-3); padding: 2px 7px; border-radius: 8px;
    white-space: nowrap; flex-shrink: 0;
  }}

  /* ── Медиафайлы ─────────────────────────────────────────────── */
  .media-grid {{
    display: grid; grid-template-columns: repeat(3,1fr); gap: 1px;
    background: var(--border);
  }}
  .media-tile {{
    background: var(--surface);
    padding: 16px 18px;
    display: flex; flex-direction: column; gap: 5px;
  }}
  .media-icon {{ font-size: 20px; }}
  .media-name {{ font-size: 12px; font-weight: 700; color: var(--text); }}
  .media-count {{ font-size: 11px; color: var(--text-3); }}
  .media-names {{
    padding: 8px 18px 12px;
    font-size: 11.5px; color: var(--text-3); line-height: 1.6;
    border-top: 1px solid var(--border);
  }}

  /* ── Гриды ──────────────────────────────────────────────────── */
  .grid-4 {{
    display: grid; grid-template-columns: repeat(2,1fr); gap: 12px;
  }}
  @media(min-width:640px) {{ .grid-4 {{ grid-template-columns: repeat(4,1fr); }} }}
  .grid-2 {{
    display: grid; grid-template-columns: 1fr; gap: 16px;
  }}
  @media(min-width:860px) {{ .grid-2 {{ grid-template-columns: 1fr 1fr; }} }}

  .empty-state {{
    padding: 32px 18px; text-align: center; color: var(--text-3);
    font-size: 13px;
  }}
  a {{ color: var(--teal-2); text-decoration: none; }}
  a:hover {{ color: var(--teal-3); }}

  /* ── YouTube-раздел ─────────────────────────────────────────── */
  .yt-card {{
    background: #0a0d14;
    border-radius: 14px;
    border: 1px solid #1a2235;
    overflow: hidden;
    box-shadow: 0 4px 32px rgba(0,0,0,0.4);
  }}
  .yt-header {{
    background: linear-gradient(135deg, #120607 0%, #1e0a0a 50%, #120607 100%);
    border-bottom: 1px solid #2d1515;
    padding: 14px 20px;
    display: flex; align-items: center; gap: 12px;
  }}
  .yt-logo {{
    width: 34px; height: 24px; background: var(--red-2);
    border-radius: 6px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
  }}
  .yt-logo::after {{
    content: ''; width: 0; height: 0; border-style: solid;
    border-width: 5.5px 0 5.5px 11px;
    border-color: transparent transparent transparent #fff;
    margin-left: 2px;
  }}
  .yt-header h2 {{ font-size: 14px; font-weight: 700; color: #f1f5f9; }}
  .yt-header-right {{ margin-left: auto; display: flex; align-items: center; gap: 8px; }}
  .yt-auth-ok   {{ background: rgba(16,185,129,0.12); color: #34d399; font-size: 11px; font-weight: 700;
                   padding: 3px 10px; border-radius: 20px; border: 1px solid rgba(16,185,129,0.2); }}
  .yt-auth-warn {{ background: rgba(239,68,68,0.12); color: #f87171; font-size: 11px; font-weight: 700;
                   padding: 3px 10px; border-radius: 20px; border: 1px solid rgba(239,68,68,0.2); }}
  .yt-studio-link {{
    font-size: 11px; font-weight: 600; color: var(--text-2);
    text-decoration: none; padding: 3px 10px; border-radius: 20px;
    border: 1px solid var(--border-2);
  }}
  .yt-studio-link:hover {{ color: var(--text); border-color: var(--text-3); background: var(--surface-3); }}

  .yt-kpi-grid {{
    display: grid; grid-template-columns: repeat(2,1fr);
    gap: 1px; background: #1a2235;
  }}
  @media(min-width:640px) {{ .yt-kpi-grid {{ grid-template-columns: repeat(4,1fr); }} }}
  .yt-kpi {{
    background: #0a0d14; padding: 18px 20px;
    display: flex; flex-direction: column; gap: 6px;
  }}
  .yt-kpi-label {{ font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
                   text-transform: uppercase; color: #475569; }}
  .yt-kpi-value {{
    font-size: 28px; font-weight: 800; line-height: 1;
    font-variant-numeric: tabular-nums;
    background: linear-gradient(135deg, #f1f5f9, #94a3b8);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  }}
  .yt-kpi-value.kv-red   {{ background: linear-gradient(135deg, #FF0000, #ff6b6b);
                             -webkit-background-clip: text; background-clip: text; }}
  .yt-kpi-value.kv-blue  {{ background: linear-gradient(135deg, var(--blue-2), #93c5fd);
                             -webkit-background-clip: text; background-clip: text; }}
  .yt-kpi-value.kv-green {{ background: linear-gradient(135deg, var(--green-2), #86efac);
                             -webkit-background-clip: text; background-clip: text; }}
  .yt-kpi-hint {{ font-size: 11px; color: #334155; }}

  .yt-table-wrap {{ overflow-x: auto; }}
  .yt-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; color: #94a3b8; }}
  .yt-table th {{
    background: #060810; color: #334155;
    font-size: 10px; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase;
    padding: 10px 16px; text-align: left; border-bottom: 1px solid #1a2235;
    white-space: nowrap;
  }}
  .yt-table td {{ padding: 11px 16px; border-bottom: 1px solid #111827; vertical-align: middle; }}
  .yt-table tr:last-child td {{ border-bottom: none; }}
  .yt-table tr:hover td {{ background: #0e1420; }}
  .yt-video-title {{ color: #e2e8f0; font-weight: 600; max-width: 280px;
                     overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .yt-video-title a {{ color: #e2e8f0; }}
  .yt-video-title a:hover {{ color: var(--red-2); }}
  .yt-badge-pub   {{ background: rgba(16,185,129,0.12); color: #34d399;
                     font-size: 10px; font-weight: 700; padding: 2px 8px;
                     border-radius: 12px; border: 1px solid rgba(16,185,129,0.2); }}
  .yt-badge-draft {{ background: #1e293b; color: #64748b;
                     font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 12px; }}
  .yt-fmt-shorts  {{ background: rgba(139,92,246,0.15); color: var(--purple-2);
                     font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 12px; }}
  .yt-fmt-lesson  {{ background: rgba(59,130,246,0.12); color: var(--blue-2);
                     font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 12px; }}
  .yt-stat {{ color: #334155; font-variant-numeric: tabular-nums; }}
  .yt-stat.hv {{ color: #64748b; }}
  .yt-empty {{ padding: 40px 20px; text-align: center; color: #1e293b; font-size: 13px; }}
  .yt-empty svg {{ opacity: 0.2; margin-bottom: 12px; }}
</style>
</head>
<body>

<!-- Шапка -->
<div class="header-bar">
  <div class="header-brand">
    <img src="/static/logo.png" alt="" class="header-logo"
         onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
    <div class="header-logo-placeholder" style="display:none">☯</div>
    <div>
      <div class="header-title">Оздоровительный цигун и ушу</div>
      <div class="header-sub">Панель управления · {now_msk} МСК · автообновление 60 с</div>
    </div>
  </div>
  <div class="header-status">
    <span class="{dot_class}"></span>
    <span class="header-status-text">{bot_mode}</span>
  </div>
</div>

<div class="wrap">

  <!-- Статус системы -->
  <div class="sec-label">Статус системы</div>
  <div class="card" style="margin-bottom:16px;">
    <div class="sys-grid">
      <div class="sys-item">
        <span class="sys-name">Бот</span>
        <span class="sys-val">{bot_status}</span>
      </div>
      <div class="sys-item">
        <span class="sys-name">Режим</span>
        <span class="sys-val">{bot_mode}</span>
      </div>
      <div class="sys-item">
        <span class="sys-name">Домен</span>
        <span class="sys-val" style="font-size:11px">{railway_domain}</span>
      </div>
      <div class="sys-item">
        <span class="sys-name">Версия</span>
        <span class="sys-val" style="color:var(--teal-2)">v4 · 2026-06-30</span>
      </div>
    </div>
  </div>

  <!-- KPI заявок -->
  <div class="sec-label">Статистика заявок</div>
  <div class="kpi-strip" style="margin-bottom:16px;">
    <div class="kpi-card c-total">
      <span class="kpi-label">Всего</span>
      <span class="kpi-value v-total">{stat_total}</span>
      <span class="kpi-hint">за всё время</span>
    </div>
    <div class="kpi-card c-today">
      <span class="kpi-label">Сегодня</span>
      <span class="kpi-value v-today">{stat_today}</span>
      <span class="kpi-hint">новых заявок</span>
    </div>
    <div class="kpi-card c-tg">
      <span class="kpi-label">Telegram</span>
      <span class="kpi-value v-tg">{stat_tg}</span>
      <span class="kpi-hint">из Telegram</span>
    </div>
    <div class="kpi-card c-vk">
      <span class="kpi-label">ВКонтакте</span>
      <span class="kpi-value v-vk">{stat_vk}</span>
      <span class="kpi-hint">из ВКонтакте</span>
    </div>
  </div>

  <!-- Заявки + Посты -->
  <div class="grid-2" style="margin-bottom:16px;">
    <div>
      <div class="sec-label">Последние заявки</div>
      <div class="card">
        <div class="card-head">
          <div class="card-head-icon ci-teal">👤</div>
          <h2>Заявки</h2>
          <div class="card-bubble">{stat_total}</div>
        </div>
        {bookings_html}
      </div>
    </div>
    <div>
      <div class="sec-label">Запланированные посты</div>
      <div class="card">
        <div class="card-head">
          <div class="card-head-icon ci-gold">⏰</div>
          <h2>Запланированные</h2>
          <div class="card-bubble">{scheduled_count}</div>
        </div>
        {scheduled_html}
      </div>
    </div>
  </div>

  <!-- Контент-план -->
  <div class="sec-label">Контент-план · {cp_month}</div>
  <div class="card" style="margin-bottom:16px;">
    <div class="card-head">
      <div class="card-head-icon ci-blue">📅</div>
      <h2>Контент-план</h2>
      <div style="margin-left:auto">{cp_status_badge}</div>
    </div>
    {cp_html}
  </div>

  <!-- Медиафайлы -->
  <div class="sec-label">Медиафайлы</div>
  <div class="card" style="margin-bottom:16px;">
    <div class="card-head">
      <div class="card-head-icon ci-purple">🎬</div>
      <h2>Медиафайлы</h2>
    </div>
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

  <!-- YouTube -->
  <div class="sec-label" style="color:var(--text-3)">YouTube · Дыхание движения</div>
  <div class="yt-card" style="margin-bottom:16px;">
    <div class="yt-header">
      <div class="yt-logo"></div>
      <h2>Дыхание движения</h2>
      <div class="yt-header-right">
        {yt_auth_badge}
        <a class="yt-studio-link" href="https://studio.youtube.com" target="_blank">YouTube Studio ↗</a>
      </div>
    </div>
    <div class="yt-kpi-grid">
      <div class="yt-kpi">
        <span class="yt-kpi-label">Подписчики</span>
        <span class="yt-kpi-value kv-red">{yt_subs}</span>
        <span class="yt-kpi-hint">на канале</span>
      </div>
      <div class="yt-kpi">
        <span class="yt-kpi-label">Просмотры</span>
        <span class="yt-kpi-value kv-blue">{yt_views}</span>
        <span class="yt-kpi-hint">всего</span>
      </div>
      <div class="yt-kpi">
        <span class="yt-kpi-label">Видео на канале</span>
        <span class="yt-kpi-value">{yt_vcount}</span>
        <span class="yt-kpi-hint">опубликовано</span>
      </div>
      <div class="yt-kpi">
        <span class="yt-kpi-label">В системе</span>
        <span class="yt-kpi-value kv-green">{yt_pub}</span>
        <span class="yt-kpi-hint">из {yt_total} подготовлено</span>
      </div>
    </div>
    {yt_table_html}
  </div>

  <!-- История постов + Площадки -->
  <div class="grid-2">
    <div>
      <div class="sec-label">История постов</div>
      <div class="card">
        <div class="card-head">
          <div class="card-head-icon ci-teal">📋</div>
          <h2>История постов</h2>
          <div class="card-bubble">{history_count}</div>
        </div>
        {history_html}
      </div>
    </div>
    <div>
      <div class="sec-label">Подписные площадки</div>
      <div class="card">
        <div class="card-head">
          <div class="card-head-icon ci-green">🔗</div>
          <h2>Площадки</h2>
          <div class="card-bubble">{links_count}</div>
        </div>
        {links_html}
      </div>
    </div>
  </div>

</div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard(username: str = Depends(require_auth)):
    try:
        scheduled  = get_scheduled_posts()
        history    = get_posts_history(30)
        links      = get_subscription_links()
        bookings   = get_bookings(25)
        stats      = get_bookings_stats()
        videos     = get_bg_videos()
        audios     = get_bg_audios()
        logo_data  = get_logo()
        cp_month   = datetime.utcnow().strftime("%Y-%m")
        cp_row     = get_content_plan(cp_month)
        yt_videos  = get_youtube_videos(20)
    except Exception as ex:
        return HTMLResponse(f"<h1>Ошибка БД: {e(str(ex))}</h1>", status_code=500)

    now_msk = (datetime.utcnow() + MOSCOW).strftime("%d.%m.%Y %H:%M")

    # ── Статус системы ──────────────────────────────────────────────────────
    import app_state
    _bs = _BADGE_STYLE
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "—")
    if app_state.admin_application:
        bot_status = f'<span class="badge-ok" {_bs}>✅ работает</span>'
        bot_mode   = "webhook" if railway_domain != "—" else "polling"
        dot_class  = "dot-online"
    else:
        bot_status = f'<span class="badge-warn" {_bs}>⚠ не запущен</span>'
        bot_mode   = "—"
        dot_class  = "dot-offline"

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
        cp_status_badge = (f'<span class="badge-cp-ok" {_bs}>✅ утверждён</span>'
                           if cp_approved else
                           f'<span class="badge-cp-draft" {_bs}>⏳ черновик</span>')
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
        cp_status_badge = f'<span class="badge-warn" {_bs}>нет плана</span>'
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
        media_rows += f'<div class="media-names">🎥 {e(names)}</div>'
    if audios:
        names = ", ".join(r[1] for r in audios[:6])
        if len(audios) > 6:
            names += f" +{len(audios)-6}"
        media_rows += f'<div class="media-names" style="border-top:none;padding-top:0">🎵 {e(names)}</div>'
    media_names_html = media_rows

    # ── YouTube ─────────────────────────────────────────────────────────────
    try:
        import youtube_api as _yt
        yt_authorized = _yt.is_authorized()
        if yt_authorized:
            try:
                yt_info = _yt.get_channel_info()
                yt_subs   = f"{yt_info.get('subscribers', 0):,}".replace(",", " ")
                yt_views  = f"{yt_info.get('views', 0):,}".replace(",", " ")
                yt_vcount = str(yt_info.get('videos', 0))
            except Exception:
                yt_subs = yt_views = yt_vcount = "—"
        else:
            yt_subs = yt_views = yt_vcount = "—"
    except Exception:
        yt_authorized = False
        yt_subs = yt_views = yt_vcount = "—"

    yt_auth_badge = ('<span class="yt-auth-ok">✓ авторизован</span>' if yt_authorized
                     else '<span class="yt-auth-warn">⚠ не авторизован</span>')

    yt_total = len(yt_videos)
    yt_pub   = sum(1 for v in yt_videos if v[4] == "published")

    if yt_videos:
        yt_rows = ""
        for row in yt_videos:
            vid_id, yt_id, title, fmt, status, views, likes, pub_at, cr_at = row
            title_cell = (
                f'<a href="https://youtu.be/{e(yt_id)}" target="_blank">{e(title or "—")}</a>'
                if yt_id else e(title or "—")
            )
            status_badge = ('<span class="yt-badge-pub">опубликовано</span>' if status == "published"
                           else '<span class="yt-badge-draft">черновик</span>')
            fmt_badge = ('<span class="yt-fmt-shorts">Shorts</span>' if fmt == "shorts"
                        else f'<span class="yt-fmt-lesson">{e(fmt or "видео")}</span>')
            views_str = f'<span class="yt-stat{"" if not views else " has-value"}">{views or "—"}</span>'
            likes_str = f'<span class="yt-stat{"" if not likes else " has-value"}">{likes or "—"}</span>'
            yt_rows += f"""<tr>
  <td class="yt-video-title">{title_cell}</td>
  <td>{fmt_badge}</td>
  <td>{status_badge}</td>
  <td>{views_str}</td>
  <td>{likes_str}</td>
  <td style="color:#475569;font-size:11px;white-space:nowrap">{msk(cr_at)}</td>
</tr>"""
        yt_table_html = f"""
<div class="yt-table-wrap">
<table class="yt-table">
  <thead><tr>
    <th>Название</th><th>Формат</th><th>Статус</th>
    <th>👁 Просмотры</th><th>❤️ Лайки</th><th>Создано</th>
  </tr></thead>
  <tbody>{yt_rows}</tbody>
</table>
</div>"""
    else:
        yt_table_html = """
<div class="yt-empty">
  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="1.5">
    <rect x="2" y="7" width="20" height="14" rx="3"/><path d="m10 10 5 2.5L10 15V10z"/>
    <path d="M8 3h8"/>
  </svg>
  <div>Видео ещё не создавались</div>
  <div style="font-size:11px;margin-top:6px;color:#1e293b">
    Нажми ▶️ YouTube → ✍️ Подготовить Shorts в боте
  </div>
</div>"""

    # ── История постов ──────────────────────────────────────────────────────
    if history:
        rows = ""
        for row in history:
            pid, platform, text, published, sched_at, created_at = row
            if published:
                badge = f'<span class="badge-pub" {_bs}>✅ опубликован</span>'
            elif sched_at:
                badge = f'<span class="badge-sched" {_bs}>⏳ запланирован</span>'
            else:
                badge = f'<span class="badge-draft" {_bs}>черновик</span>'
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
        dot_class      = dot_class,
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
        yt_auth_badge  = yt_auth_badge,
        yt_subs        = yt_subs,
        yt_views       = yt_views,
        yt_vcount      = yt_vcount,
        yt_total       = yt_total,
        yt_pub         = yt_pub,
        yt_table_html  = yt_table_html,
    )
    return HTMLResponse(html)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/youtube/callback", response_class=HTMLResponse)
async def youtube_oauth_callback(request: Request, code: str = "", error: str = ""):
    """Принимает OAuth2-код от Google, сохраняет токены в БД."""
    if error:
        return HTMLResponse(
            f"<h2>Ошибка авторизации YouTube</h2><p>{error}</p>",
            status_code=400,
        )
    if not code:
        return HTMLResponse("<h2>Код авторизации отсутствует</h2>", status_code=400)
    try:
        import youtube_api as _yt
        _yt.exchange_code(code)
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:40px;text-align:center">
            <h2>✅ YouTube авторизован!</h2>
            <p>Канал «Дыхание движения» подключён.</p>
            <p>Можешь закрыть эту страницу и вернуться в Telegram-бот.</p>
            </body></html>
        """)
    except Exception as e:
        return HTMLResponse(
            f"<h2>Ошибка обмена токена</h2><p>{e}</p>",
            status_code=500,
        )


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
