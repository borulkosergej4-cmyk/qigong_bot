import os
import logging
from collections import defaultdict

import anthropic
import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY", "").strip()
OPENROUTER_KEY  = os.getenv("OPENROUTER_API_KEY", "").strip()
GROQ_KEY        = os.getenv("GROQ_API_KEY", "").strip()

CLAUDE_MODEL        = "claude-haiku-4-5-20251001"
_OR_MODEL           = "google/gemma-2-27b-it:free"
_OR_FALLBACK        = "meta-llama/llama-3.3-70b-instruct:free"
_GROQ_MODEL         = "llama-3.3-70b-versatile"
_GROQ_FALLBACK      = "llama-3.1-8b-instant"

_MAX_HISTORY = 20

_histories: dict[int, list[dict]] = defaultdict(list)

_anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

_openrouter_client = None
if OPENROUTER_KEY:
    _openrouter_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_KEY,
        max_retries=0,
        http_client=httpx.Client(trust_env=False),
    )

_groq_client = None
if GROQ_KEY:
    _groq_client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=GROQ_KEY,
        max_retries=0,
        http_client=httpx.Client(trust_env=False),
    )


def _call_openrouter(messages: list, system: str, model: str) -> str:
    resp = _openrouter_client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}] + messages,
        max_tokens=1000,
        timeout=30,
    )
    return resp.choices[0].message.content.strip()


def _call_groq(messages: list, system: str, model: str) -> str:
    resp = _groq_client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}] + messages,
        max_tokens=1000,
        timeout=30,
    )
    return resp.choices[0].message.content.strip()


class BaseAgent:
    def __init__(self, system_prompt: str):
        self.system = system_prompt

    def _history(self, user_id: int) -> list:
        return _histories[user_id]

    def clear_history(self, user_id: int):
        _histories.pop(user_id, None)

    def seed(self, user_id: int, content: str):
        """Добавить стартовое сообщение ассистента, чтобы AI не представлялся заново."""
        h = self._history(user_id)
        if not h:
            h.append({"role": "assistant", "content": content})

    _SCHEDULE_KEYWORDS = (
        "расписани", "занятия", "занятие", "тренировк", "записат",
        "когда", "во сколько", "в какое время", "ближайш", "сегодня",
        "завтра", "неделя", "онлайн", "офлайн", "прийти",
        "где", "адрес", "место", "зал", "парк", "проходят",
    )

    async def ask(self, user_id: int, text: str) -> str:
        history = self._history(user_id)
        is_first = len(history) == 0
        history.append({"role": "user", "content": text})
        if len(history) > _MAX_HISTORY:
            history[:] = history[-_MAX_HISTORY:]

        system = self.system

        # Определяем, нужно ли подтянуть расписание
        text_lower = text.lower().strip()
        _needs_schedule = any(kw in text_lower for kw in self._SCHEDULE_KEYWORDS)
        # Короткий утвердительный ответ после сообщения о расписании
        if not _needs_schedule and text_lower in ("да", "ок", "хочу", "покажи", "конечно", "давай", "да,", "да.", "ладно"):
            _last_bot = next(
                (m["content"] for m in reversed(history[:-1]) if m["role"] == "assistant"), ""
            )
            if any(kw in _last_bot.lower() for kw in ("расписани", "занятия", "занятие", "записат", "когда", "время")):
                _needs_schedule = True

        if _needs_schedule:
            try:
                import asyncio
                from data.mobifitness import get_schedule_text, get_today_info
                schedule = await asyncio.to_thread(get_schedule_text)
                today = get_today_info()
                _no_data = any(p in schedule for p in (
                    "Нет предстоящих", "не загружено", "временно недоступно", "недоступно"
                ))
                if _no_data:
                    system = (
                        f"{self.system}\n\n"
                        f"Сейчас: {today}\n\n"
                        f"‼️ РАСПИСАНИЕ СЕЙЧАС НЕДОСТУПНО — API вернул пустой ответ.\n"
                        f"АБСОЛЮТНЫЙ ЗАПРЕТ: не называть никаких дней, времени, дат и мест занятий.\n"
                        f"Единственно допустимый ответ по расписанию: "
                        f"«Актуальное расписание уточните у Сергея в канале @baguazhangspb — "
                        f"там всегда есть свежая информация.»\n"
                        f"Никаких «обычно», «как правило», «примерно» — это тоже запрещено."
                    )
                else:
                    system = (
                        f"{self.system}\n\n"
                        f"Сейчас: {today}\n\n"
                        f"РАСПИСАНИЕ ЗАНЯТИЙ СЕРГЕЯ (актуальные данные из системы):\n{schedule}\n\n"
                        f"ВАЖНО: называй ТОЛЬКО занятия из этого списка. "
                        f"Не добавляй занятия, которых нет выше. Не придумывай дни, время и места."
                    )
            except Exception as e:
                logger.warning(f"Не удалось получить расписание: {e}")
                from datetime import datetime, timedelta, timezone
                MOSCOW_TZ = timezone(timedelta(hours=3))
                now = datetime.now(MOSCOW_TZ)
                system = (
                    f"{self.system}\n\n"
                    f"Сейчас: {now.strftime('%d.%m.%Y %H:%M')} МСК\n\n"
                    f"‼️ РАСПИСАНИЕ ВРЕМЕННО НЕДОСТУПНО. "
                    f"ЗАПРЕЩЕНО называть время, дни и места занятий. "
                    f"Скажи пользователю: «Расписание уточните у Сергея в канале @baguazhangspb.»"
                )

        reply = await self._generate(history, system=system)

        history.append({"role": "assistant", "content": reply})
        return reply

    @staticmethod
    def _postprocess(text: str) -> str:
        import re
        text = text.replace("**", "").replace("*", "")
        _DAY_MAP = [
            (r'\bНаMonday\b',       'В понедельник'),
            (r'\bна\s+Monday\b',    'в понедельник'),
            (r'\bMonday\b',         'Понедельник'),
            (r'\bНаTuesday\b',      'Во вторник'),
            (r'\bна\s+Tuesday\b',   'во вторник'),
            (r'\bTuesday\b',        'Вторник'),
            (r'\bНаWednesday\b',    'В среду'),
            (r'\bна\s+Wednesday\b', 'в среду'),
            (r'\bWednesday\b',      'Среда'),
            (r'\bНаThursday\b',     'В четверг'),
            (r'\bна\s+Thursday\b',  'в четверг'),
            (r'\bThursday\b',       'Четверг'),
            (r'\bНаFriday\b',       'В пятницу'),
            (r'\bна\s+Friday\b',    'в пятницу'),
            (r'\bFriday\b',         'Пятница'),
            (r'\bНаSaturday\b',     'В субботу'),
            (r'\bна\s+Saturday\b',  'в субботу'),
            (r'\bSaturday\b',       'Суббота'),
            (r'\bНаSunday\b',       'В воскресенье'),
            (r'\bна\s+Sunday\b',    'в воскресенье'),
            (r'\bSunday\b',         'Воскресенье'),
        ]
        for pattern, replacement in _DAY_MAP:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # практика → занятие (все падежи)
        _PRACTICE_MAP = [
            (r'\bпрактиках\b',   'занятиях'),
            (r'\bпрактиками\b',  'занятиями'),
            (r'\bпрактикам\b',   'занятиям'),
            (r'\bпрактикой\b',   'занятием'),
            (r'\bпрактику\b',    'занятие'),
            (r'\bпрактике\b',    'занятии'),
            (r'\bпрактики\b',    'занятия'),
            (r'\bпрактика\b',    'занятие'),
        ]
        for pattern, replacement in _PRACTICE_MAP:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # Нормализация названия комплекса
        text = re.sub(r'\bДа\s+[Уу]\b', 'Да ВУ', text)

        if '\n' not in text:
            text = re.sub(r'([.!?])\s+([А-ЯЁ«(])', r'\1\n\n\2', text)
        return text.strip()

    async def _generate(self, messages: list, system: str = None) -> str:
        sys = system or self.system

        # 1. Claude Haiku
        if _anthropic_client:
            try:
                resp = _anthropic_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=1000,
                    system=sys,
                    messages=messages,
                )
                return self._postprocess(resp.content[0].text)
            except Exception as e:
                logger.warning(f"Claude error: {e}")

        # 2. OpenRouter
        if _openrouter_client:
            for model in (_OR_MODEL, _OR_FALLBACK):
                try:
                    return self._postprocess(_call_openrouter(messages, sys, model))
                except Exception as e:
                    logger.warning(f"OpenRouter {model} error: {e}")

        # 3. Groq
        if _groq_client:
            for model in (_GROQ_MODEL, _GROQ_FALLBACK):
                try:
                    return self._postprocess(_call_groq(messages, sys, model))
                except Exception as e:
                    logger.warning(f"Groq {model} error: {e}")

        return "Мудрость требует времени. Попробуй спросить чуть позже."
