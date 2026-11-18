# ticker_render.py
"""Рендер бегущей строки в PNG с поддержкой цветных эмодзи.

Зачем: FFmpeg drawtext (freetype) умеет только монохромные глифы и не
рисует цветные эмодзи. Поэтому строку собираем картинкой через Pillow:
  * обычный текст — шрифтом канала (DejaVuSans/Arial),
  * эмодзи — шрифтом Segoe UI Emoji с embedded_color=True.
Готовый PNG с прозрачным фоном накладывается фильтром overlay и
прокручивается по x. Пересборка картинки — только при смене текста.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Диапазоны эмодзи + склейки (VS16, ZWJ), чтобы не разрывать составные эмодзи
EMOJI_RE = re.compile(
    "([\U0001F000-\U0001FAFF\u2600-\u26FF\uFE0F\u200D]+)"
)

EMOJI_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\seguiemj.ttf",
    r"C:\Windows\Fonts\seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
)


def has_emoji(text: str) -> bool:
    return bool(EMOJI_RE.search(text or ""))


def _find_emoji_font() -> str | None:
    for p in EMOJI_FONT_CANDIDATES:
        if Path(p).is_file():
            return p
    return None


def _font(path: str | None, size: int, fallback: str | None = None):
    for candidate in (path, fallback, r"C:\Windows\Fonts\arial.ttf"):
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _runs(text: str) -> list[tuple[str, bool]]:
    """Делит строку на куски: (текст, это_эмодзи)."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in EMOJI_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def render_ticker_png(
    text: str,
    out_path: str | Path,
    font_path: str | None = None,
    font_size: int = 28,
    font_color: str = "#FFFFFF",
    padding: int = 8,
    emoji_scale: float = 1.0,
) -> tuple[int, int]:
    """Рисует строку в PNG с прозрачным фоном. Возвращает (ширина, высота)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text = (text or "").replace("\r", " ").replace("\n", " ")
    scale = max(0.5, min(3.0, float(emoji_scale or 1.0)))
    emoji_size = max(8, int(font_size * scale))

    text_font = _font(font_path, font_size)
    emoji_path = _find_emoji_font()
    emoji_font = _font(emoji_path, emoji_size, fallback=r"C:\Windows\Fonts\seguisym.ttf") if emoji_path else _font(
        r"C:\Windows\Fonts\seguisym.ttf", emoji_size
    )

    parts = _runs(text) if has_emoji(text) else [(text, False)]
    if not parts:
        parts = [(" ", False)]

    # --- измерение ---
    probe = Image.new("RGBA", (16, 16))
    pd = ImageDraw.Draw(probe)
    widths: list[float] = []
    for chunk, is_emoji in parts:
        f = emoji_font if is_emoji else text_font
        try:
            widths.append(pd.textlength(chunk, font=f))
        except Exception:
            widths.append(font_size * 0.6 * len(chunk))

    total_w = int(sum(widths)) + padding * 2
    height = int(max(font_size, emoji_size) * 1.45) + padding * 2

    img = Image.new("RGBA", (max(1, total_w), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    baseline_y = padding + int(max(font_size, emoji_size) * 1.05)
    x = float(padding)
    for (chunk, is_emoji), w in zip(parts, widths):
        f = emoji_font if is_emoji else text_font
        color = font_color if not is_emoji else None
        try:
            if is_emoji and hasattr(draw, "text") and emoji_path:
                # embedded_color включает рендер COLR/CPAL и CBDT-глифов
                draw.text((x, baseline_y), chunk, font=f, anchor="ls",
                          embedded_color=True)
            else:
                draw.text((x, baseline_y), chunk, font=f, anchor="ls",
                          fill=color or font_color)
        except Exception:
            try:
                draw.text((x, baseline_y), chunk, font=f, anchor="ls",
                          fill=font_color)
            except Exception:
                pass
        x += w

    tmp = out_path.with_suffix(".tmp.png")
    img.save(tmp, "PNG")
    tmp.replace(out_path)
    return img.width, img.height
