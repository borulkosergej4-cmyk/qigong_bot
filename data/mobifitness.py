import httpx
from datetime import datetime, timedelta, timezone

MOSCOW_TZ = timezone(timedelta(hours=3))

CLUB_ID = "2391"
BASE_URL = f"https://mobifitness.ru/api/v8/club/{CLUB_ID}"
TOKEN = "b6d7d372-8ea5-46c9-991f-8bf4d5deeecc"

# Показывать только занятия этого тренера (регистронезависимо, частичное совпадение)
TRAINER_FILTER = "Сергей"

# Маппинг фрагментов названий залов из Mobifitness → читаемое место с адресом
# Реальные названия из API:
#   "Парк Сосновка площадка 1"         → парк Сосновка
#   "Зал 1 на Просвещения 43"          → Парк Молл
#   "Зал 2 на Просвещения 43"          → Парк Молл
#   "Зал в Новом Девяткино ул Арсенальная 5А" → Новое Девяткино
ROOM_MAP = {
    "сосновка":    "парк Сосновка (Тореза 72)",
    "просвещения": "Парк Молл (Просвещения 43)",
    "девяткино":   "Новое Девяткино (Арсенальная 5а)",
    "арсенальная": "Новое Девяткино (Арсенальная 5а)",
}

HEADERS = {
    "authorization": f"Bearer {TOKEN}",
    "accept": "application/json, text/javascript, */*; q=0.01",
    "x-angular-widget": "true",
    "x-requested-with": "XMLHttpRequest",
    "referer": f"https://mobifitness.ru/schedule-widget/?code={CLUB_ID}&type=schedule",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

DAYS_RU = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}

TITLE_MAP = {
    "qigong": "Цигун",
    "цигун": "Цигун",
    "оздоровительный цигун": "Оздоровительный цигун",
    "wushu": "Ушу",
    "ушу": "Ушу",
    "bagua": "Багуачжан",
    "bagua zhang": "Багуачжан",
    "багуачжан": "Багуачжан",
    "taichi": "Тайцзицюань",
    "tai chi": "Тайцзицюань",
    "тайцзи": "Тайцзицюань",
    "тайцзицюань": "Тайцзицюань",
    "meditation": "Медитация",
    "медитация": "Медитация",
    "stretching": "Растяжка",
    "стретчинг": "Растяжка",
}


def _normalize_title(title: str) -> str:
    key = title.strip().lower()
    return TITLE_MAP.get(key, title)


def _normalize_room(room_title: str) -> str:
    if not room_title:
        return ""
    key = room_title.strip().lower()
    for fragment, label in ROOM_MAP.items():
        if fragment in key:
            return label
    return room_title.strip()


def get_current_week() -> tuple[int, int]:
    now = datetime.now(MOSCOW_TZ)
    return now.year, now.isocalendar()[1]


def get_today_info() -> str:
    now = datetime.now(MOSCOW_TZ)
    day_name = DAYS_RU.get(now.weekday(), "")
    return f"{day_name}, {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')} МСК"


def fetch_schedule(year: int = None, week: int = None) -> dict:
    if not year or not week:
        year, week = get_current_week()
    url = f"{BASE_URL}/schedule.json?year={year}&week={week}"
    with httpx.Client(trust_env=False, timeout=10) as client:
        resp = client.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()


def format_schedule(data: dict) -> str:
    items = data.get("schedule", [])
    if not items:
        return "Расписание на эту неделю пока не загружено."

    now = datetime.now(MOSCOW_TZ)
    today_date = now.date()
    tomorrow_date = today_date + timedelta(days=1)

    by_day: dict = {}
    for item in items:
        change = item.get("change")
        if change and change.get("type") == "canceled":
            continue

        activity = item.get("activity", {})
        name = _normalize_title(activity.get("title", "Занятие"))
        trainers = item.get("trainers", [])
        trainer_name = trainers[0].get("title", "") if trainers else ""

        # Показываем только занятия Сергея
        if TRAINER_FILTER and TRAINER_FILTER.lower() not in trainer_name.lower():
            continue

        # Зал/место проведения
        room_raw = (
            item.get("room") or item.get("hall") or item.get("location") or {}
        )
        if isinstance(room_raw, dict):
            room_raw = room_raw.get("title", "")
        location = _normalize_room(str(room_raw))

        dt_str = item.get("datetime", "")
        length = item.get("length", 0)

        if not dt_str:
            continue
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MOSCOW_TZ)
            if dt < now:
                continue
            date_key = dt.astimezone(MOSCOW_TZ).date()
            time_str = dt.astimezone(MOSCOW_TZ).strftime("%H:%M")
            if length:
                dt_end = dt + timedelta(minutes=length)
                time_str += f"–{dt_end.astimezone(MOSCOW_TZ).strftime('%H:%M')}"
        except Exception:
            continue

        available = item.get("availableSlots", "")
        total = item.get("totalSlots", "")
        try:
            av, tot = int(available), int(total)
            if av == 0:
                places_str = "мест нет"
            elif av == tot:
                places_str = f"свободно {av} мест"
            else:
                places_str = f"свободно {av} из {tot}"
        except Exception:
            places_str = ""

        line = f"  {time_str} — {name}"
        if location:
            line += f" | {location}"
        if places_str:
            line += f" [{places_str}]"

        by_day.setdefault(date_key, []).append(line)

    if not by_day:
        return "Нет предстоящих занятий Сергея на этой неделе."

    lines = []
    for date_key in sorted(by_day):
        day_name = DAYS_RU.get(date_key.weekday(), "")
        date_str = date_key.strftime("%d.%m")
        if date_key == today_date:
            header = f"Сегодня ({day_name} {date_str}):"
        elif date_key == tomorrow_date:
            header = f"Завтра ({day_name} {date_str}):"
        else:
            header = f"{day_name} {date_str}:"
        lines.append(header)
        lines.extend(by_day[date_key])

    return "\n".join(lines)


def get_schedule_text() -> str:
    try:
        now_year, now_week = get_current_week()
        data = fetch_schedule(now_year, now_week)
        text = format_schedule(data)

        # Добавляем следующую неделю
        next_week_date = datetime.now(MOSCOW_TZ) + timedelta(weeks=1)
        ny, nw = next_week_date.year, next_week_date.isocalendar()[1]
        data2 = fetch_schedule(ny, nw)
        text2 = format_schedule(data2)
        if text2 and "не загружено" not in text2 and "Нет предстоящих" not in text2:
            text += "\n\n" + text2

        return text
    except Exception as e:
        return f"Расписание временно недоступно ({e})."
