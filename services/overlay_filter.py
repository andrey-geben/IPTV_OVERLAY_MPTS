"""
Сборщик фильтров FFmpeg для overlay.
drawbox (полоса) + drawtext (бегущий текст), синхронизированные по enable.
"""

from .overlay_models import OverlayText


def _normalize_color(color: str) -> str:
    color = (color or "").strip()
    if not color: return "white"
    if color.startswith("#"): return "0x" + color[1:]
    return color


def _to_hex_color(color: str) -> str:
    color = (color or "black").strip().lower()
    named = {
        "black": "0x000000", "white": "0xFFFFFF", "red": "0xFF0000",
        "green": "0x00FF00", "blue": "0x0000FF", "yellow": "0xFFFF00"
    }
    if color in named: return named[color]
    if color.startswith("#"): return "0x" + color[1:].upper()
    if color.startswith("0x"): return color.upper()
    return "0x000000"


def _escape_drawtext(s: str, is_path: bool = False) -> str:
    if is_path: s = s.replace("\\", "/")
    s = s.replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    return s


def build_drawtext_filter(t: OverlayText) -> str:
    font_path = _escape_drawtext(t.font_path, is_path=True)
    text = _escape_drawtext(t.text, is_path=False)

    filters = []

    # -----подсказка------------
    # band_height — высота полосы (шрифт + отступы)
    # y_offset — отступ от низа (ограничен 0..300)
    band_height = t.font_size + 40
    y_offset = max(0, min(300, int(t.y_offset)))

    # -----подсказка------------
    # ВАЖНО: используем 'h', а не 'ih'!
    # drawbox понимает оба ('h' и 'ih'), а drawtext — ТОЛЬКО 'h'/'main_h'/'H'.
    # Если в drawtext подставить 'ih', FFmpeg упадёт:
    #   "Undefined constant or missing '(' in 'ih'"
    # и весь фильтр (вместе с полосой) не запустится.
    band_y = f"h-{band_height}-{y_offset}"

    # -----подсказка------------
    # Сколько секунд текст виден в кадре — чтобы синхронизировать полосу.
    # 'speed' — пикс/сек. Ширина текста ≈ символы * размер * 0.6.
    period = max(1, t.repeat_period_sec)
    text_width_approx = t.font_size * len(t.text) * 0.6
    on_screen_sec = (720 + text_width_approx) / max(1, t.speed)

    # -----подсказка------------
    # Внутри одинарных кавычек FFmpeg НЕ требует экранировать запятые —
    # наоборот, '\,' внутри '...' ломает парсер выражения.
    #   enable='lt(mod(t,60),12.5)'  ← правильно
    #   enable='lt(mod(t\,60)\,12.5)' ← не работает
    enable_expr = f"lt(mod(t,{period}),{on_screen_sec:.1f})"

    alpha = max(0.0, min(1.0, float(t.box_alpha)))
    if t.box_enabled and alpha > 0:
        box_color = _to_hex_color(t.box_color)
        filters.append(
            f"drawbox=x=0:y={band_y}:w=iw:h={band_height}:"
            f"color={box_color}@{alpha}:thickness=fill:enable='{enable_expr}'"
        )
        # Центрируем текст по вертикали внутри полосы.
        # th = text_h, валидная переменная drawtext.
        text_y = f"{band_y}+({band_height}-th)/2"
    else:
        text_y = f"h-th-{y_offset}"

    # Движение текста: справа налево со скоростью speed.
    # '\,' нужен, чтобы FFmpeg не спутал запятую с разделителем фильтров.
    x_expr = f"w-mod(t*{t.speed}\\,{period}*{t.speed})"
    font_color = _normalize_color(t.font_color)

    filters.append(
        f"drawtext=fontfile='{font_path}':text='{text}':fontsize={t.font_size}:"
        f"fontcolor={font_color}@{t.font_alpha}:x={x_expr}:y={text_y}"
    )
    return ",".join(filters)