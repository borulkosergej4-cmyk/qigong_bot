"""
Генерация Reel-видео на Python (Pillow + ffmpeg).
"""
import subprocess
import tempfile
import shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 720, 1280
FPS = 30

# ── Цвета (природная тема: нефрит, бамбук, золото) ────────────────────────
C_BG_TOP    = (10,  40,  28)    # глубокий лесной зелёный
C_BG_BOT    = (30,  70,  48)    # тёмный нефрит
C_GOLD      = (200, 165,  45)   # состаренное золото
C_GOLD2     = (240, 205,  80)   # яркое золото
C_JADE      = (70,  145, 110)   # нефритовый
C_EARTH     = (160, 110,  70)   # тёплая земля
C_CREAM     = (248, 242, 225)   # пергамент

C_LBG_TOP   = (245, 248, 238)
C_LBG_BOT   = (215, 238, 220)
C_DARK      = (32,  45,  38)

# ── Логотип ───────────────────────────────────────────────────────────────
_LOGO_PATH = Path(__file__).parent / "static" / "logo.png"
_logo_cache: dict = {}

def _load_logo(width: int = 220, dark: bool = False) -> "Image.Image | None":
    key = (width, dark)
    if key in _logo_cache:
        return _logo_cache[key]
    if not _LOGO_PATH.exists():
        _logo_cache[key] = None
        return None
    try:
        img = Image.open(_LOGO_PATH).convert("RGBA")
        orig_w, orig_h = img.size
        new_h = int(width * orig_h / orig_w)
        img = img.resize((width, new_h), Image.LANCZOS)
        data = list(img.getdata())
        corners = [img.getpixel((0, 0)), img.getpixel((img.width - 1, 0)),
                   img.getpixel((0, img.height - 1))]
        avg_brightness = sum(p[0] + p[1] + p[2] for p in corners) / (3 * 3)
        is_dark_bg = avg_brightness < 128
        if is_dark_bg:
            if dark:
                img.putdata([
                    (r, g, b, 0) if (r < 50 and g < 50 and b < 50) else (30, 40, 32, a)
                    for r, g, b, a in data
                ])
            else:
                img.putdata([
                    (r, g, b, 0) if (r < 50 and g < 50 and b < 50) else (r, g, b, a)
                    for r, g, b, a in data
                ])
        else:
            if dark:
                img.putdata([
                    (r, g, b, 0) if (r > 215 and g > 215 and b > 215) else (r, g, b, a)
                    for r, g, b, a in data
                ])
            else:
                img.putdata([
                    (r, g, b, 0) if (r > 215 and g > 215 and b > 215) else (255, 255, 255, a)
                    for r, g, b, a in data
                ])
        _logo_cache[key] = img
        return img
    except Exception:
        _logo_cache[key] = None
        return None


def clear_logo_cache():
    _logo_cache.clear()


def _paste_logo(frame: Image.Image, logo: "Image.Image | None",
                cx: int, cy: int, alpha: float) -> None:
    if alpha <= 0.01 or logo is None:
        return
    lw, lh = logo.size
    x0, y0 = cx - lw // 2, cy - lh // 2
    if alpha >= 0.99:
        tmp = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        tmp.paste(logo, (x0, y0))
        frame.alpha_composite(tmp)
    else:
        r, g, b, a_ch = logo.split()
        a_scaled = a_ch.point(lambda v: int(v * alpha))
        faded = Image.merge("RGBA", (r, g, b, a_scaled))
        tmp = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        tmp.paste(faded, (x0, y0))
        frame.alpha_composite(tmp)


# ── Шрифты ────────────────────────────────────────────────────────────────
_FONT_CACHE: dict = {}

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    for p in candidates:
        if Path(p).exists():
            try:
                f = ImageFont.truetype(p, size)
                _FONT_CACHE[key] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default(size)
    _FONT_CACHE[key] = f
    return f


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3

def _sanitize(text: str) -> str:
    return (text
        .replace("₽", " р.")
        .replace("🔥", "").replace("✅", "").replace("❌", "")
        .replace("📱", "").replace("✈️", "").replace("🌿", "")
    )

def _fade(frame: int, start_f: int, end_f: int) -> float:
    if frame <= start_f: return 0.0
    if frame >= end_f:   return 1.0
    return _ease_out((frame - start_f) / (end_f - start_f))

def _lerp(a, b, t):
    return a + (b - a) * max(0.0, min(1.0, t))

def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        bb = font.getbbox(test)
        if bb[2] - bb[0] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]

def _fit_font(text: str, max_w: int, size_max: int, bold: bool = True) -> "ImageFont.FreeTypeFont":
    size = size_max
    while size > 28:
        f = _font(size, bold=bold)
        bb = f.getbbox(text)
        if bb[2] - bb[0] <= max_w:
            return f
        size -= 6
    return _font(28, bold=bold)

def _text_alpha(img: Image.Image, _draw,
                text: str, font: ImageFont.FreeTypeFont,
                cx: int, cy: int, color: tuple, alpha: float,
                anchor: str = "mm"):
    if alpha <= 0.01:
        return
    tmp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((cx, cy), text, font=font, fill=(*color[:3], int(255 * alpha)), anchor=anchor)
    img.alpha_composite(tmp)

def _rect_alpha(img: Image.Image, x0, y0, x1, y1, color, alpha):
    if alpha <= 0.01: return
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 <= x0 or y1 <= y0: return
    tmp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.rectangle([x0, y0, x1, y1], fill=(*color[:3], int(255 * alpha)))
    img.alpha_composite(tmp)

def _rrect_alpha(img: Image.Image, x0, y0, x1, y1, r, color, alpha):
    if alpha <= 0.01: return
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 <= x0 or y1 <= y0: return
    r = max(0, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
    tmp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=(*color[:3], int(255 * alpha)))
    img.alpha_composite(tmp)

def _circle_alpha(img: Image.Image, cx, cy, r, color, alpha):
    if alpha <= 0.01 or r <= 0:
        return
    tmp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color[:3], int(255 * alpha)))
    img.alpha_composite(tmp)


def _make_dark_bg() -> Image.Image:
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        r = int(C_BG_TOP[0] + (C_BG_BOT[0] - C_BG_TOP[0]) * t)
        g = int(C_BG_TOP[1] + (C_BG_BOT[1] - C_BG_TOP[1]) * t)
        b = int(C_BG_TOP[2] + (C_BG_BOT[2] - C_BG_TOP[2]) * t)
        d.line([(0, y), (W, y)], fill=(r, g, b))
    return bg

def _make_light_bg() -> Image.Image:
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        r = int(C_LBG_TOP[0] + (C_LBG_BOT[0] - C_LBG_TOP[0]) * t)
        g = int(C_LBG_TOP[1] + (C_LBG_BOT[1] - C_LBG_TOP[1]) * t)
        b = int(C_LBG_TOP[2] + (C_LBG_BOT[2] - C_LBG_TOP[2]) * t)
        d.line([(0, y), (W, y)], fill=(r, g, b))
    return bg


# ── МОТИВАЦИЯ ─────────────────────────────────────────────────────────────

def render_motivation(quote: str, subtext: str, output_path: str,
                      bg_video: str | None = None,
                      audio_path: str | None = None,
                      duration_secs: int = 15) -> str:
    quote, subtext = _sanitize(quote), _sanitize(subtext)
    n = FPS * max(5, duration_secs)
    tmp = tempfile.mkdtemp(prefix="py_reel_")
    bg_dir: str | None = None
    try:
        bg = _make_dark_bg()
        if bg_video and Path(bg_video).exists():
            bg_dir = tempfile.mkdtemp(prefix="py_bg_")
            if not _extract_bg_frames(bg_video, n, bg_dir):
                shutil.rmtree(bg_dir, ignore_errors=True)
                bg_dir = None
        font_q  = _font(72, bold=True)
        font_s  = _font(28, bold=False)
        lh = 90
        max_w = W - 130

        all_lines = _wrap(quote, font_q, max_w)
        block_h = len(all_lines) * lh
        block_y = H // 2 - block_h // 2
        logo_img = _load_logo(220)

        for i in range(n):
            frame = Image.new("RGBA", (W, H))
            if bg_dir:
                bp = Path(bg_dir) / f"bg{i+1:04d}.jpg"
                frame.paste((Image.open(str(bp)) if bp.exists() else bg).convert("RGB"))
                _rect_alpha(frame, 0, 0, W, H, (0, 0, 0), 0.55)
            else:
                frame.paste(bg)

            _circle_alpha(frame, W // 2 + 80, -60,  420, C_JADE,  0.28)
            _circle_alpha(frame, -80,          H // 2, 300, C_EARTH, 0.22)
            _circle_alpha(frame, W + 60,       H - 100, 380, C_GOLD, 0.18)

            logo_a = _fade(i, 0, int(FPS * 0.6)) * 0.85
            _paste_logo(frame, logo_img, W // 2, 90, logo_a)

            gw = int(W * 0.55 * _fade(i, 0, int(FPS * 1.0)))
            _rect_alpha(frame, (W - gw) // 2, block_y - 60,
                        (W + gw) // 2, block_y - 55, C_GOLD2, _fade(i, 0, int(FPS * 0.6)))

            card_alpha = _fade(i, int(FPS * 0.3), int(FPS * 0.7)) * 0.22
            _rrect_alpha(frame, 50, block_y - 80,
                         W - 50, block_y + block_h + 80, 24, C_CREAM, card_alpha)

            q_alpha = _fade(i, int(FPS * 0.5), int(FPS * 1.0))
            progress = _ease_out((i - int(FPS * 0.7)) / max(1, FPS * 2.2))
            visible = max(0, min(int(progress * len(quote)), len(quote)))
            vis_lines = _wrap(quote[:visible], font_q, max_w)

            for li, line in enumerate(vis_lines):
                cy = block_y + li * lh + lh // 2
                _text_alpha(frame, None, line, font_q, W // 2 + 2, cy + 3,
                            (0, 0, 0), q_alpha * 0.5)
                _text_alpha(frame, None, line, font_q, W // 2, cy, C_CREAM, q_alpha)

            if visible < len(quote) and vis_lines:
                blink = (i % int(FPS * 0.5)) < int(FPS * 0.25)
                if blink:
                    last = vis_lines[-1]
                    bb = font_q.getbbox(last)
                    li_idx = len(vis_lines) - 1
                    cx = W // 2 + (bb[2] - bb[0]) // 2 + 8
                    cy = block_y + li_idx * lh + lh // 2
                    _text_alpha(frame, None, "|", font_q, cx, cy, C_GOLD2, 0.9)

            ln2_a = _fade(i, int(FPS * 2.4), int(FPS * 2.9))
            _rect_alpha(frame, (W - 80) // 2, block_y + block_h + 40,
                        (W + 80) // 2, block_y + block_h + 45, C_GOLD, ln2_a)

            sub_a = _fade(i, int(FPS * 2.7), int(FPS * 3.3)) * 0.65
            _text_alpha(frame, None, subtext.upper(), font_s, W // 2,
                        block_y + block_h + 80, C_CREAM, sub_a)

            corner_a = _fade(i, int(FPS * 3.0), int(FPS * 3.6)) * 0.4
            clen = 30
            for cx2, cy2, dx, dy in [(60, 60, 1, 1), (W-60, 60, -1, 1),
                                     (60, H-60, 1, -1), (W-60, H-60, -1, -1)]:
                _rect_alpha(frame, cx2, cy2, cx2 + dx * clen, cy2 + dy * 3, C_GOLD, corner_a)
                _rect_alpha(frame, cx2, cy2, cx2 + dx * 3, cy2 + dy * clen, C_GOLD, corner_a)

            frame.convert("RGB").save(f"{tmp}/f{i:04d}.jpg", quality=90)

        _stitch(tmp, n, output_path, audio_path=audio_path)
        return output_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if bg_dir:
            shutil.rmtree(bg_dir, ignore_errors=True)


# ── ПРОМО ─────────────────────────────────────────────────────────────────

def render_promo(headline: str, price: str, subtext: str, cta: str, output_path: str,
                 bg_video: str | None = None,
                 audio_path: str | None = None,
                 duration_secs: int = 15) -> str:
    headline, price, subtext, cta = (_sanitize(x) for x in (headline, price, subtext, cta))
    n = FPS * max(6, duration_secs)
    tmp = tempfile.mkdtemp(prefix="py_reel_")
    bg_dir: str | None = None
    try:
        bg = _make_light_bg()
        if bg_video and Path(bg_video).exists():
            bg_dir = tempfile.mkdtemp(prefix="py_bg_")
            if not _extract_bg_frames(bg_video, n, bg_dir):
                shutil.rmtree(bg_dir, ignore_errors=True)
                bg_dir = None
        on_video = bg_dir is not None
        font_h    = _font(76, bold=True)
        font_p    = _font(108, bold=True)
        font_s    = _font(36, bold=False)
        font_c    = _font(34, bold=False)

        max_w = W - 100
        h_lines = _wrap(headline, font_h, max_w)
        h_lh    = 88
        h_block = len(h_lines) * h_lh
        bb_p  = font_p.getbbox(price)
        ph    = bb_p[3] - bb_p[1]
        price_badge_h = ph + 34
        sub_lines = _wrap(subtext, font_s, max_w)
        sub_block = len(sub_lines) * 50
        GAP = 44
        total_h = h_block + GAP + price_badge_h + GAP + sub_block + GAP + 72
        y0 = (H - total_h) // 2
        y_logo   = y0 - 145
        y_sep    = y0 - 38
        y_h_top  = y0
        y_price  = y0 + h_block + GAP
        y_sub    = y_price + price_badge_h + GAP
        y_cta    = y_sub + sub_block + GAP
        logo_img = _load_logo(220, dark=not on_video)

        for i in range(n):
            frame = Image.new("RGBA", (W, H))
            if bg_dir:
                bp = Path(bg_dir) / f"bg{i+1:04d}.jpg"
                frame.paste((Image.open(str(bp)) if bp.exists() else bg).convert("RGB"))
                _rect_alpha(frame, 0, 0, W, H, (0, 0, 0), 0.50)
            else:
                frame.paste(bg)

            _circle_alpha(frame, -80,    100, 280, C_JADE,  0.30)
            _circle_alpha(frame, W + 80, H - 120, 260, C_EARTH, 0.25)
            _circle_alpha(frame, W // 2, -50,  180, C_GOLD, 0.15)

            _paste_logo(frame, logo_img, W // 2, y_logo,
                        _fade(i, 0, int(FPS * 0.6)) * 0.90)

            sep_a = _fade(i, int(FPS * 0.4), int(FPS * 0.9))
            dw = int(120 * sep_a)
            _rect_alpha(frame, (W - dw) // 2, y_sep, (W + dw) // 2, y_sep + 3, C_GOLD, sep_a)

            h_a = _fade(i, int(FPS * 0.5), int(FPS * 1.1))
            for li, line in enumerate(h_lines):
                cy = y_h_top + li * h_lh + h_lh // 2
                _text_alpha(frame, None, line, font_h, W // 2, cy, C_CREAM if on_video else C_DARK, h_a)

            p_a = _ease_out((i - int(FPS * 1.0)) / max(1, int(FPS * 0.9)))
            if p_a > 0.01:
                bb  = font_p.getbbox(price)
                pw  = bb[2] - bb[0]
                px0 = (W - pw - 80) // 2
                _rrect_alpha(frame, px0 - 3, y_price - 3,
                             px0 + pw + 83, y_price + price_badge_h + 3, 20, C_DARK, p_a * 0.08)
                _rrect_alpha(frame, px0, y_price,
                             px0 + pw + 80, y_price + price_badge_h, 20, C_GOLD, p_a)
                _text_alpha(frame, None, price, font_p,
                            W // 2, y_price + price_badge_h // 2, C_DARK, p_a)

            st_a = _fade(i, int(FPS * 1.6), int(FPS * 2.2)) * 0.75
            for li, line in enumerate(sub_lines):
                _text_alpha(frame, None, line, font_s, W // 2,
                            y_sub + li * 50 + 25, C_CREAM if on_video else C_DARK, st_a)

            cta_a = _fade(i, int(FPS * 2.2), int(FPS * 2.8))
            if cta_a > 0.01:
                bb  = font_c.getbbox(cta)
                cw  = bb[2] - bb[0]
                cx0 = (W - cw - 90) // 2
                _rrect_alpha(frame, cx0, y_cta, cx0 + cw + 90, y_cta + 68, 34,
                             C_GOLD if on_video else C_DARK, cta_a)
                _text_alpha(frame, None, cta, font_c, W // 2, y_cta + 34,
                            C_DARK if on_video else C_CREAM, cta_a)

            frame.convert("RGB").save(f"{tmp}/f{i:04d}.jpg", quality=90)

        _stitch(tmp, n, output_path, audio_path=audio_path)
        return output_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if bg_dir:
            shutil.rmtree(bg_dir, ignore_errors=True)


# ── АНОНС ─────────────────────────────────────────────────────────────────

def render_announcement(class_type: str, trainer: str, date: str, time: str,
                        spots: str, tagline: str, output_path: str,
                        bg_video: str | None = None,
                        audio_path: str | None = None,
                        duration_secs: int = 15) -> str:
    class_type, trainer, date, time = (_sanitize(x) for x in (class_type, trainer, date, time))
    spots, tagline = _sanitize(spots), _sanitize(tagline)
    n = FPS * max(7, duration_secs)
    tmp = tempfile.mkdtemp(prefix="py_reel_")
    bg_dir: str | None = None
    try:
        bg = _make_light_bg()
        if bg_video and Path(bg_video).exists():
            bg_dir = tempfile.mkdtemp(prefix="py_bg_")
            if not _extract_bg_frames(bg_video, n, bg_dir):
                shutil.rmtree(bg_dir, ignore_errors=True)
                bg_dir = None
        on_video = bg_dir is not None
        font_ct   = _fit_font(class_type, W - 60, 100, bold=True)
        font_med  = _font(46, bold=False)
        font_time = _font(76, bold=True)
        font_date = _font(46, bold=True)
        font_sm   = _font(36, bold=False)
        font_tag  = _font(34, bold=False)
        logo_img  = _load_logo(220, dark=not on_video)

        for i in range(n):
            frame = Image.new("RGBA", (W, H))
            if bg_dir:
                bp = Path(bg_dir) / f"bg{i+1:04d}.jpg"
                frame.paste((Image.open(str(bp)) if bp.exists() else bg).convert("RGB"))
                _rect_alpha(frame, 0, 0, W, H, (0, 0, 0), 0.50)
            else:
                frame.paste(bg)

            _circle_alpha(frame, W - 40,  -40,  300, C_JADE, 0.28)
            _circle_alpha(frame, -40, H + 30, 250, C_EARTH, 0.22)

            cy = 200
            _paste_logo(frame, logo_img, W // 2, cy - 35,
                        _fade(i, 0, int(FPS * 0.5)) * 0.90)

            ct_a = _fade(i, int(FPS * 0.3), int(FPS * 0.9))
            _text_alpha(frame, None, class_type, font_ct, W // 2, cy + 150,
                        C_CREAM if on_video else C_DARK, ct_a)

            sep_a = _fade(i, int(FPS * 0.7), int(FPS * 1.2))
            sw = int(100 * sep_a)
            _rect_alpha(frame, (W - sw) // 2, cy + 218, (W + sw) // 2, cy + 223, C_GOLD, sep_a)

            _text_alpha(frame, None, trainer, font_med, W // 2, cy + 280,
                        C_CREAM if on_video else C_DARK,
                        _fade(i, int(FPS * 0.8), int(FPS * 1.3)) * 0.75)

            dt_a = _fade(i, int(FPS * 1.1), int(FPS * 1.6))
            if dt_a > 0.01:
                bx, bw, bh = (W - 560) // 2, 560, 164
                by = cy + 330
                _rrect_alpha(frame, bx, by, bx + bw, by + bh, 22, C_DARK, dt_a)
                _text_alpha(frame, None, date, font_date, W // 2, by + 44, C_CREAM, dt_a)
                _text_alpha(frame, None, time, font_time, W // 2, by + 118, C_GOLD2, dt_a)

            if spots:
                spots_a = _fade(i, int(FPS * 1.5), int(FPS * 2.0))
                _text_alpha(frame, None, spots, font_sm, W // 2, cy + 545,
                            (200, 90, 70), spots_a)

            if tagline:
                tg_a = _fade(i, int(FPS * 1.9), int(FPS * 2.5))
                if tg_a > 0.01:
                    bb = font_tag.getbbox(tagline)
                    tw = bb[2] - bb[0]
                    tx = (W - tw - 100) // 2
                    ty = cy + 600
                    _rrect_alpha(frame, tx, ty, tx + tw + 100, ty + 70, 35, C_JADE, tg_a * 0.85)
                    _text_alpha(frame, None, tagline, font_tag,
                                (tx * 2 + tw + 100) // 2, ty + 35, C_DARK, tg_a)

            frame.convert("RGB").save(f"{tmp}/f{i:04d}.jpg", quality=90)

        _stitch(tmp, n, output_path, audio_path=audio_path)
        return output_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if bg_dir:
            shutil.rmtree(bg_dir, ignore_errors=True)


# ── Видео-фон ─────────────────────────────────────────────────────────────

def _extract_bg_frames(video_path: str, n_frames: int, out_dir: str) -> bool:
    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1",
        "-i", video_path,
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "-r", str(FPS),
        "-frames:v", str(n_frames),
        f"{out_dir}/bg%04d.jpg",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def _extract_bg_audio(video_path: str, out_path: str) -> bool:
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "aac", "-b:a", "128k", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode == 0 and Path(out_path).exists()


def _stitch(frames_dir: str, n_frames: int, output: str, audio_path: str | None = None):
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    duration = n_frames / FPS
    if audio_path and Path(audio_path).exists():
        fade_start = max(0.0, duration - 1.5)
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", f"{frames_dir}/f%04d.jpg",
            "-stream_loop", "-1", "-i", audio_path,
            "-map", "0:v", "-map", "1:a",
            "-vframes", str(n_frames),
            "-t", str(duration),
            "-af", f"afade=t=out:st={fade_start:.2f}:d=1.5",
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
            output,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", f"{frames_dir}/f%04d.jpg",
            "-vframes", str(n_frames),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
            output,
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-400:]}")
