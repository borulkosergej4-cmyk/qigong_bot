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
        max_tokens=600,
        timeout=30,
    )
    return resp.choices[0].message.content.strip()


def _call_groq(messages: list, system: str, model: str) -> str:
    resp = _groq_client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}] + messages,
        max_tokens=600,
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
        if is_first:
            system = system + "\n\nЭто первое сообщение от этого человека — обязательно представься."
        if any(kw in text.lower() for kw in self._SCHEDULE_KEYWORDS):
            try:
                import asyncio
                from data.mobifitness import get_schedule_text, get_today_info
                schedule = await asyncio.to_thread(get_schedule_text)
                today = get_today_info()
                system = (
                    f"{self.system}\n\n"
                    f"Сейчас: {today}\n\n"
                    f"Актуальное расписание занятий:\n{schedule}"
                )
            except Exception as e:
                logger.warning(f"Не удалось получить расписание: {e}")

        reply = await self._generate(history, system=system)

        history.append({"role": "assistant", "content": reply})
        return reply

    @staticmethod
    def _postprocess(text: str) -> str:
        import re
        text = re.sub(r"\*+", "", text)
        return text.strip()

    async def _generate(self, messages: list, system: str = None) -> str:
        sys = system or self.system

        # 1. Claude Haiku
        if _anthropic_client:
            try:
                resp = _anthropic_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=600,
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
