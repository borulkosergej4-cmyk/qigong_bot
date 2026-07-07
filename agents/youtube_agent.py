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
import re
import textwrap
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Ссылки, которые добавляются в описание каждого видео ────────────────────
_BOOSTY_URL      = "https://boosty.to/s.borulko"
_TG_CHANNEL      = os.getenv("TG_CHANNEL", "@baguazhangspb").strip()
_TG_CHANNEL_URL  = f"https://t.me/{_TG_CHANNEL.lstrip('@')}"

# ── Термины, которые не должны склоняться (цигун, Бадуаньцзин и т.п.) ───────
_DECLENSION_FIX = re.compile(r'\b(цигун|Бадуаньцзин|ушу|Тайцзицюань)(а|у|ом|е)\b')


def _fix_declensions(text: str) -> str:
    return _DECLENSION_FIX.sub(lambda m: m.group(1), text) if text else text


# ── Номер серии в теме/заголовке для бейджа на превью. Реальные заголовки
# используют словесные порядковые числительные («второе упражнение»,
# «первое упражнение»), а не цифры, поэтому ищем оба варианта — но только
# впритык к ключевому слову, иначе «5 минут в день» ложно даёт «День 5».
_EPISODE_KEYWORDS = ("день", "урок", "упражнение")
_ORDINAL_STEMS = {
    "перв": 1, "втор": 2, "трет": 3, "четверт": 4, "четвёрт": 4, "пят": 5,
    "шест": 6, "седьм": 7, "восьм": 8, "девят": 9, "десят": 10,
}
_WORD_RE = re.compile(r'[^\W\d_]+|\d+', re.UNICODE)


def _extract_episode_label(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        tokens = _WORD_RE.findall(text.lower())
        for i, tok in enumerate(tokens):
            if tok not in _EPISODE_KEYWORDS:
                continue
            neighbors = tokens[max(i - 1, 0):i] + tokens[i + 1:i + 2]
            for nb in neighbors:
                if nb.isdigit() and 1 <= int(nb) <= 21:
                    return f"{tok.capitalize()} {nb}"
                for stem, num in _ORDINAL_STEMS.items():
                    if nb.startswith(stem):
                        return f"{tok.capitalize()} {num}"
    return ""

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
        4. ПРИЗЫВ К ДЕЙСТВИЮ — обязательная финальная реплика (последние 3–5 сек),
           которую Сергей произносит на камеру. Она должна называть конкретный
           продукт — программу «Бадуаньцзин за 21 день» на Boosty — а не общее
           «подписывайся». Программа БЕСПЛАТНАЯ (лид-магнит, не платный
           продукт) — это обязательно проговаривать явно, «бесплатно» снимает
           главное возражение и должно звучать в самой реплике, а не
           подразумеваться. Пример тона: «Это упражнение — часть полной
           бесплатной программы „Бадуаньцзин за 21 день“, она у меня на
           Boosty, ссылка в описании». Ссылка в описании видео сама по себе
           не работает как воронка — её почти никто не читает, особенно в
           Shorts, поэтому CTA обязан прозвучать голосом внутри самого ролика.

        Стиль: прямой, живой, без канцелярита. Не «Цигун помогает», а
        «Три минуты этого упражнения снижают давление — я покажу как».

        ВАЖНО: термины «цигун», «Бадуаньцзин», «ушу», «Тайцзицюань» — НЕ склонять
        ни в одном падеже. Всегда именительный падеж, независимо от контекста:
        «занятие цигун» (не «цигуна»), «комплекс Бадуаньцзин» (не «Бадуаньцзина»),
        «практика ушу» (не «ушу» склонённое), «стиль Тайцзицюань» (не «Тайцзицюаня»).

        ЗАПРЕЩЕНО:
        - Называть конкретные диагнозы/болезни (остеохондроз, гипертония, грыжа и т.п.)
          как то, что упражнение лечит или устраняет — только «снимает напряжение»,
          «помогает при дискомфорте», без названия болезни.
        - Обещать результат к конкретному сроку («за 3 недели», «уже через неделю»
          избавит/уберёт) — вместо этого «регулярная практика помогает».
        - Сравнивать эффект с лекарствами или медициной («лучше таблеток» и т.п.).

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
        if "бадуаньцзин" in topic.lower() or "упражнение" in topic.lower():
            from data.knowledge import BADUANJIN_TECHNIQUE
            prompt += f"\n\nСправочник по технике (используй для точности, не копируй дословно):\n{BADUANJIN_TECHNIQUE}"

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
        - Хэштеги: 5–8 штук для описания видео (не путать с тегами выше). Формат «#слово»
          без пробелов внутри. Смесь тематических («#цигун», «#ушу», «#здоровье») и
          форматных («#shorts» для Shorts-видео).
        - Категория: 26 (How-to) или 17 (Sports) — укажи код.

        ВАЖНО: хэштеги указывай ТОЛЬКО в отдельном поле "hashtags" JSON.
        В самом тексте поля "description" хэштегов (символ #) быть не должно —
        они добавляются в описание автоматически по коду отдельным блоком в
        конце. Хэштег, вставленный ИИ прямо в середину текста описания, не
        проходит проверку регистра/формата и может задвоить бренд-хэштег в
        другом написании.

        ВАЖНО: термины «цигун», «Бадуаньцзин», «ушу», «Тайцзицюань» — НЕ склонять
        ни в одном падеже, ни в заголовке, ни в описании, ни в тегах. Всегда
        именительный падеж: «занятие цигун» (не «цигуна»), «комплекс Бадуаньцзин»
        (не «Бадуаньцзина»).

        Если передан справочник по технике упражнения — название упражнения в
        заголовке и описании должно ТОЧНО совпадать со справочником (например,
        «Стрельба в позе лучника», а не «Стрельба из лука»). Не перефразируй
        и не придумывай своё название вместо того, что в справочнике.

        ЗАПРЕЩЕНО:
        - Называть конкретные диагнозы/болезни (остеохондроз, гипертония и т.п.)
          как то, что упражнение лечит или устраняет.
        - Обещать результат к конкретному сроку («за 3 недели» и т.п.).
        - Сравнивать эффект с лекарствами («лучше таблеток» и т.п.).

        Отвечай строго в формате JSON:
        {
          "title": "...",
          "description": "...",
          "tags": ["...", "..."],
          "hashtags": ["#...", "#..."],
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
        if "бадуаньцзин" in topic.lower() or "упражнение" in topic.lower():
            from data.knowledge import BADUANJIN_TECHNIQUE
            prompt += f"\n\nСправочник по технике (название упражнения бери точно отсюда):\n{BADUANJIN_TECHNIQUE}"
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
            return {"title": raw[:100], "description": raw, "tags": [], "hashtags": [], "category_id": "26"}
        try:
            return json.loads(m.group())
        except Exception:
            return {"title": raw[:100], "description": raw, "tags": [], "hashtags": [], "category_id": "26"}


# ═══════════════════════════════════════════════════════════════════════════════
# ThumbnailAgent — генератор превью
# ═══════════════════════════════════════════════════════════════════════════════

class ThumbnailAgent:
    """Генерирует thumbnail 1280×720 для YouTube через Pillow."""

    def generate(self, title: str, subtitle: str = "", style: str = "dark", episode_label: str = "") -> bytes:
        """
        title:         главный текст (крупно)
        subtitle:      подзаголовок (мелко)
        style:         'dark' | 'violet' | 'minimal'
        episode_label: если задан («День 3», «Урок 2») — рисуется как бейдж
                        в левом верхнем углу, чтобы серийные видео визуально
                        читались как прогрессия, а не разрозненные ролики
        Возвращает JPEG bytes.
        """
        from PIL import Image, ImageDraw

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

        # Бейдж серии («День N» / «Урок N») — левый верхний угол
        if episode_label:
            self._draw_episode_badge(draw, episode_label)

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

    def _draw_episode_badge(self, draw, label: str):
        """Скруглённый бейдж с номером серии в левом верхнем углу превью."""
        font = self._load_font(38)
        bbox = draw.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        pad_x, pad_y = 24, 14
        x0, y0 = 30, 30
        x1 = x0 + text_w + pad_x * 2
        y1 = y0 + text_h + pad_y * 2
        draw.rounded_rectangle([x0, y0, x1, y1], radius=14, fill=_PALETTE["bg_accent"])
        draw.text((x0 + pad_x, y0 + pad_y - bbox[1]), label, font=font, fill=_PALETTE["text_white"])

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
        script = _fix_declensions(
            self.script_agent.generate(topic, format_=format_, duration_hint=duration_hint)
        )
        logger.info("Сценарий готов")

        # 2. SEO
        seo = self.seo_agent.optimize(topic, script_excerpt=script[:800])
        logger.info(f"SEO готов: {seo.get('title', '?')}")
        title = _fix_declensions(seo.get("title", topic))
        tags = [_fix_declensions(t) for t in seo.get("tags", [])]

        # 3. Превью
        thumbnail = self.thumbnail_agent.generate(
            title=title,
            subtitle=topic if topic != title else "",
            style=thumb_style,
            episode_label=_extract_episode_label(topic, title),
        )
        logger.info("Превью сгенерировано")

        # CTA на бесплатную программу + ссылки на канал/Boosty + хэштеги —
        # добавляем в код детерминированно, а не полагаемся на то, что модель
        # вспомнит об этом каждый раз (раньше это было единственным источником
        # и периодически терялось).
        description = _fix_declensions(seo.get("description", ""))
        # Подстраховка: если модель всё же вставила хэштег прямо в текст
        # описания (несмотря на инструкцию в SYSTEM), вырезаем — настоящие
        # хэштеги добавляются отдельным блоком ниже, из отдельного поля.
        description = re.sub(r'#\S+', '', description)
        description = re.sub(r'[ \t]{2,}', ' ', description)
        description = re.sub(r'\n{3,}', '\n\n', description).strip()
        program_line = (
            "🎁 Бесплатная программа «Бадуаньцзин за 21 день» — 8 упражнений "
            "с прогрессией, ждёт вас на Boosty."
        )
        links_block = (
            f"📌 Telegram-канал: {_TG_CHANNEL_URL}\n"
            f"💛 Видеоуроки и поддержка на Boosty: {_BOOSTY_URL}"
        )
        description = description.rstrip() + "\n\n" + program_line + "\n\n" + links_block

        # Хэштеги: убираем пробелы внутри (ломают хэштег на YouTube) и
        # гарантируем брендовый тег — не полагаемся, что модель его не забудет
        hashtags = [f"#{h.lstrip('#')}".replace(" ", "") for h in seo.get("hashtags", [])]
        if "#ДыханиеДвижения" not in hashtags:
            hashtags.append("#ДыханиеДвижения")
        if hashtags:
            description = description.rstrip() + "\n\n" + " ".join(hashtags)

        return {
            "topic":          topic,
            "format":         format_,
            "script":         script,
            "title":          title,
            "description":    description,
            "tags":           tags,
            "hashtags":       hashtags,
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
