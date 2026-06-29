"""
Мультиагентная система для YouTube-канала «Дыхание движения».

Агенты:
  ScriptAgent     — пишет сценарий видео по теме
  SeoAgent        — оптимизирует заголовок, описание и теги
  ThumbnailAgent  — генерирует превью-картинку через Pillow
  AnalyticsAgent  — читает статистику канала и даёт рекомендации
  YoutubeMaster   — оркестрирует все агенты: от идеи до публикации
"""

import logging
import os
import textwrap
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Цвета и шрифты для превью ────────────────────────────────────────────────
_FONTS_DIR = Path(__file__).parent.parent / "fonts"
_THUMB_W   = 1280
_THUMB_H   = 720

_PALETTE = {
    "bg_dark":    (18, 18, 28),
    "bg_accent":  (138, 43, 226),   # фиолетовый — цигун/медитация
    "text_white": (255, 255, 255),
    "text_gold":  (255, 215, 80),
    "overlay":    (0, 0, 0, 160),
}


# ═══════════════════════════════════════════════════════════════════════════════
# ScriptAgent — сценарист
# ═══════════════════════════════════════════════════════════════════════════════

class ScriptAgent:
    """Пишет сценарий видео для канала «Дыхание движения»."""

    SYSTEM = textwrap.dedent("""
        Ты сценарист YouTube-канала «Дыхание движения» (@дыхание_движения).
        Канал посвящён оздоровительному цигун и ушу для русскоязычной аудитории.
        Ведёт канал Сергей — преподаватель с многолетним опытом в Санкт-Петербурге.

        Форматы видео:
        - Shorts (до 60 сек): 150–200 слов, 1 конкретный приём или факт
        - Урок (10–20 мин): 1500–2500 слов, полная структура с демонстрацией
        - Обзор (5–10 мин): 800–1200 слов, теория + польза

        Структура сценария:
        1. КРЮЧОК (первые 3–5 сек) — неожиданный факт или вопрос
        2. ОБЕЩАНИЕ — что зритель получит за N минут
        3. ОСНОВНАЯ ЧАСТЬ — шаги/приёмы/объяснения
        4. ПРИЗЫВ К ДЕЙСТВИЮ — подписка, следующее видео, занятие

        Стиль: прямой, живой, без канцелярита. Не «Цигун помогает», а
        «Три минуты этого упражнения снижают давление — я покажу как».

        Отвечай только сценарием, без вводных фраз.
    """).strip()

    def generate(self, topic: str, format_: str = "shorts", duration_hint: str = "") -> str:
        """
        topic: тема видео, например «Бадуаньцзин упражнение 3 — разведение лука»
        format_: 'shorts' | 'lesson' | 'review'
        duration_hint: подсказка о хронометраже
        """
        prompt = f"Тема: {topic}\nФормат: {format_}"
        if duration_hint:
            prompt += f"\nДлительность: {duration_hint}"

        return self._call(prompt)

    def _call(self, prompt: str) -> str:
        from agents.base_agent import _anthropic_client, CLAUDE_MODEL, \
            _openrouter_client, _call_openrouter, _OR_MODEL, _OR_FALLBACK

        messages = [{"role": "user", "content": prompt}]

        if _anthropic_client:
            try:
                resp = _anthropic_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=3000,
                    system=self.SYSTEM,
                    messages=messages,
                )
                return resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"ScriptAgent Claude error: {e}")

        if _openrouter_client:
            for model in (_OR_MODEL, _OR_FALLBACK):
                try:
                    return _call_openrouter(messages, self.SYSTEM, model)
                except Exception as e:
                    logger.warning(f"ScriptAgent OpenRouter {model}: {e}")

        raise RuntimeError("ScriptAgent: все AI-провайдеры недоступны")


# ═══════════════════════════════════════════════════════════════════════════════
# SeoAgent — SEO-оптимизатор
# ═══════════════════════════════════════════════════════════════════════════════

class SeoAgent:
    """Генерирует заголовок, описание и теги для YouTube-видео."""

    SYSTEM = textwrap.dedent("""
        Ты SEO-специалист YouTube-канала «Дыхание движения» о цигун и ушу.
        Твоя задача — максимизировать органический охват видео в русскоязычном YouTube.

        Правила:
        - Заголовок: 50–70 символов, ключевое слово в начале, конкретная польза
        - Описание: 500–1000 символов. Первые 2 строки — самые важные (видны без раскрытия).
          Включи временные метки, если есть главы. Ссылки в конце.
        - Теги: 10–15 штук. Начни с точных («цигун для начинающих»), затем широкие («цигун»,
          «китайская гимнастика», «оздоровительная гимнастика», «здоровье»).
        - Категория: 26 (How-to) или 17 (Sports) — укажи код.

        Отвечай строго в формате JSON:
        {
          "title": "...",
          "description": "...",
          "tags": ["...", "..."],
          "category_id": "26"
        }
    """).strip()

    def optimize(self, topic: str, script_excerpt: str = "") -> dict:
        """
        topic: тема видео
        script_excerpt: первые 300 слов сценария (для контекста)
        Возвращает dict: title, description, tags, category_id
        """
        prompt = f"Тема видео: {topic}"
        if script_excerpt:
            excerpt = script_excerpt[:800]
            prompt += f"\n\nФрагмент сценария:\n{excerpt}"

        raw = self._call(prompt)
        return self._parse(raw)

    def _call(self, prompt: str) -> str:
        from agents.base_agent import _anthropic_client, CLAUDE_MODEL, \
            _openrouter_client, _call_openrouter, _OR_MODEL, _OR_FALLBACK

        messages = [{"role": "user", "content": prompt}]

        if _anthropic_client:
            try:
                resp = _anthropic_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=1000,
                    system=self.SYSTEM,
                    messages=messages,
                )
                return resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"SeoAgent Claude error: {e}")

        if _openrouter_client:
            for model in (_OR_MODEL, _OR_FALLBACK):
                try:
                    return _call_openrouter(messages, self.SYSTEM, model)
                except Exception as e:
                    logger.warning(f"SeoAgent OpenRouter {model}: {e}")

        raise RuntimeError("SeoAgent: все AI-провайдеры недоступны")

    @staticmethod
    def _parse(raw: str) -> dict:
        import json, re
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            return {"title": raw[:100], "description": raw, "tags": [], "category_id": "26"}
        try:
            return json.loads(m.group())
        except Exception:
            return {"title": raw[:100], "description": raw, "tags": [], "category_id": "26"}


# ═══════════════════════════════════════════════════════════════════════════════
# ThumbnailAgent — генератор превью
# ═══════════════════════════════════════════════════════════════════════════════

class ThumbnailAgent:
    """Генерирует thumbnail 1280×720 для YouTube через Pillow."""

    def generate(self, title: str, subtitle: str = "", style: str = "dark") -> bytes:
        """
        title:    главный текст (крупно)
        subtitle: подзаголовок (мелко)
        style:    'dark' | 'violet' | 'minimal'
        Возвращает JPEG bytes.
        """
        from PIL import Image, ImageDraw, ImageFont, ImageFilter

        img  = Image.new("RGB", (_THUMB_W, _THUMB_H), _PALETTE["bg_dark"])
        draw = ImageDraw.Draw(img)

        if style == "violet":
            self._draw_gradient(img, _PALETTE["bg_dark"], _PALETTE["bg_accent"])
        elif style == "minimal":
            img.paste(_PALETTE["bg_accent"], [0, 0, _THUMB_W, _THUMB_H])

        # Декоративная полоса
        draw.rectangle([0, _THUMB_H - 8, _THUMB_W, _THUMB_H], fill=_PALETTE["bg_accent"])
        draw.rectangle([0, 0, 8, _THUMB_H], fill=_PALETTE["bg_accent"])

        # Иероглиф «氣» как фоновый элемент
        self._draw_bg_glyph(draw)

        # Логотип канала (если есть)
        self._try_paste_logo(img)

        # Заголовок
        title_font = self._load_font(90)
        wrapped    = self._wrap(title, title_font, _THUMB_W - 120)
        y = 180 if subtitle else (_THUMB_H - len(wrapped) * 110) // 2
        for line in wrapped:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            w    = bbox[2] - bbox[0]
            draw.text(((_THUMB_W - w) // 2, y), line,
                      font=title_font, fill=_PALETTE["text_white"])
            y += 110

        # Подзаголовок
        if subtitle:
            sub_font = self._load_font(46)
            sub_wrap = self._wrap(subtitle, sub_font, _THUMB_W - 160)
            y += 20
            for line in sub_wrap:
                bbox = draw.textbbox((0, 0), line, font=sub_font)
                w    = bbox[2] - bbox[0]
                draw.text(((_THUMB_W - w) // 2, y), line,
                          font=sub_font, fill=_PALETTE["text_gold"])
                y += 58

        # Название канала внизу
        ch_font = self._load_font(32)
        draw.text((30, _THUMB_H - 52), "Дыхание движения",
                  font=ch_font, fill=_PALETTE["text_gold"])

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    # ── Вспомогательные методы ─────────────────────────────────────────────────

    def _load_font(self, size: int):
        from PIL import ImageFont
        candidates = [
            _FONTS_DIR / "DejaVuSans-Bold.ttf",
            _FONTS_DIR / "NotoSans-Bold.ttf",
            _FONTS_DIR / "Arial.ttf",
        ]
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except Exception:
                    pass
        return ImageFont.load_default()

    @staticmethod
    def _wrap(text: str, font, max_width: int) -> list[str]:
        from PIL import ImageDraw, Image
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        words  = text.split()
        lines  = []
        line   = ""
        for word in words:
            test = (line + " " + word).strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines or [text]

    @staticmethod
    def _draw_gradient(img, color1: tuple, color2: tuple):
        from PIL import Image
        w, h = img.size
        for y in range(h):
            t = y / h
            r = int(color1[0] * (1 - t) + color2[0] * t)
            g = int(color1[1] * (1 - t) + color2[1] * t)
            b = int(color1[2] * (1 - t) + color2[2] * t)
            for x in range(w):
                img.putpixel((x, y), (r, g, b))

    @staticmethod
    def _draw_bg_glyph(draw):
        """Рисует полупрозрачный иероглиф 氣 как фоновый элемент."""
        try:
            from PIL import ImageFont
            font_path = _FONTS_DIR / "NotoSansCJK-Regular.ttc"
            if not font_path.exists():
                return
            font = ImageFont.truetype(str(font_path), 480)
            draw.text((820, 100), "氣", font=font, fill=(255, 255, 255, 18))
        except Exception:
            pass

    @staticmethod
    def _try_paste_logo(img):
        logo_path = Path("static/logo.png")
        if not logo_path.exists():
            return
        try:
            from PIL import Image
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((120, 120))
            img.paste(logo, (_THUMB_W - 140, 20), logo)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# AnalyticsAgent — аналитик канала
# ═══════════════════════════════════════════════════════════════════════════════

class AnalyticsAgent:
    """Читает статистику канала и возвращает текстовый отчёт + рекомендации."""

    SYSTEM = textwrap.dedent("""
        Ты аналитик YouTube-канала «Дыхание движения» (цигун и ушу).
        На входе — статистика канала и последних видео.
        Твоя задача: дать чёткий отчёт (3–5 предложений) и 2–3 конкретные
        рекомендации по развитию канала. Без воды и общих фраз.
        Формат: сначала цифры, потом рекомендации.
    """).strip()

    def analyze(self, channel_info: dict, recent_videos: list[dict]) -> str:
        lines = [
            f"Канал: {channel_info.get('title', '?')}",
            f"Подписчики: {channel_info.get('subscribers', 0):,}",
            f"Всего просмотров: {channel_info.get('views', 0):,}",
            f"Видео: {channel_info.get('videos', 0)}",
        ]
        if recent_videos:
            lines.append("\nПоследние видео:")
            for v in recent_videos[:5]:
                lines.append(
                    f"  [{v['date']}] {v['title']} — "
                    f"{v.get('views', '?')} просмотров, "
                    f"{v.get('likes', '?')} лайков"
                )

        prompt = "\n".join(lines)
        return self._call(prompt)

    def _call(self, prompt: str) -> str:
        from agents.base_agent import _anthropic_client, CLAUDE_MODEL, \
            _openrouter_client, _call_openrouter, _OR_MODEL, _OR_FALLBACK

        messages = [{"role": "user", "content": prompt}]

        if _anthropic_client:
            try:
                resp = _anthropic_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=600,
                    system=self.SYSTEM,
                    messages=messages,
                )
                return resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"AnalyticsAgent Claude error: {e}")

        if _openrouter_client:
            for model in (_OR_MODEL, _OR_FALLBACK):
                try:
                    return _call_openrouter(messages, self.SYSTEM, model)
                except Exception as e:
                    logger.warning(f"AnalyticsAgent OpenRouter {model}: {e}")

        return "Аналитика временно недоступна — все AI-провайдеры не отвечают."


# ═══════════════════════════════════════════════════════════════════════════════
# YoutubeMaster — главный оркестратор
# ═══════════════════════════════════════════════════════════════════════════════

class YoutubeMaster:
    """
    Оркестратор: берёт тему → генерирует сценарий → SEO → превью →
    сохраняет в БД → готов к загрузке на YouTube.
    """

    def __init__(self):
        self.script_agent    = ScriptAgent()
        self.seo_agent       = SeoAgent()
        self.thumbnail_agent = ThumbnailAgent()
        self.analytics_agent = AnalyticsAgent()

    def prepare_video(
        self,
        topic: str,
        format_: str = "shorts",
        thumb_style: str = "dark",
        duration_hint: str = "",
    ) -> dict:
        """
        Полный пайплайн подготовки видео.
        Возвращает dict:
          script, title, description, tags, category_id,
          thumbnail_bytes (JPEG)
        """
        logger.info(f"YoutubeMaster: готовлю видео «{topic}» [{format_}]")

        # 1. Сценарий
        script = self.script_agent.generate(topic, format_=format_, duration_hint=duration_hint)
        logger.info("Сценарий готов")

        # 2. SEO
        seo = self.seo_agent.optimize(topic, script_excerpt=script[:800])
        logger.info(f"SEO готов: {seo.get('title', '?')}")

        # 3. Превью
        thumbnail = self.thumbnail_agent.generate(
            title=seo.get("title", topic),
            subtitle=topic if topic != seo.get("title", "") else "",
            style=thumb_style,
        )
        logger.info("Превью сгенерировано")

        return {
            "topic":          topic,
            "format":         format_,
            "script":         script,
            "title":          seo.get("title", topic),
            "description":    seo.get("description", ""),
            "tags":           seo.get("tags", []),
            "category_id":    seo.get("category_id", "26"),
            "thumbnail_bytes": thumbnail,
        }

    def get_analytics_report(self) -> str:
        """Читает статистику канала и возвращает отчёт."""
        import youtube_api as yt
        if not yt.is_authorized():
            return "YouTube не авторизован. Запусти авторизацию в боте."
        try:
            channel_info   = yt.get_channel_info()
            recent_videos  = yt.get_recent_videos(max_results=10)
            # Добавляем детальную статистику к каждому видео
            for v in recent_videos:
                try:
                    stats = yt.get_video_stats(v["id"])
                    v.update(stats)
                except Exception:
                    pass
            return self.analytics_agent.analyze(channel_info, recent_videos)
        except Exception as e:
            return f"Ошибка получения аналитики: {e}"

    def publish_prepared(self, video_path: str, prepared: dict) -> str:
        """
        Загружает готовое видео на YouTube.
        prepared — результат prepare_video().
        Возвращает YouTube video ID.
        """
        import youtube_api as yt
        is_shorts = prepared.get("format") == "shorts"
        video_id = yt.upload_video(
            file_path=video_path,
            title=prepared["title"],
            description=prepared["description"],
            tags=prepared["tags"],
            category_id=prepared.get("category_id", "26"),
            shorts=is_shorts,
        )
        # Устанавливаем превью
        if prepared.get("thumbnail_bytes"):
            try:
                yt.set_thumbnail(video_id, prepared["thumbnail_bytes"])
            except Exception as e:
                logger.warning(f"Не удалось установить превью: {e}")

        return video_id


# Синглтоны для использования в admin_bot
script_agent    = ScriptAgent()
seo_agent       = SeoAgent()
thumbnail_agent = ThumbnailAgent()
analytics_agent = AnalyticsAgent()
youtube_master  = YoutubeMaster()
