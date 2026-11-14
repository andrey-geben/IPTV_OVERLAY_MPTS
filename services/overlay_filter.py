"""
Сборщик фильтров FFmpeg для overlay (drawbox + drawtext).
"""

from .overlay_models import OverlayText


def _normalize_color(color: str) -> str:
    """
    Приводит цвет к формату, который понимает FFmpeg:
      #RRGGBB   → 0xRRGGBB
      #RRGGBBAA → 0xRRGGBBAA
      black     → black (имя)
      0xRRGGBB  → 0xRRGGBB (уже правильный)
    """
    color = (color or "").strip()
    if not color:
        return "black"
    if color.startswith("#"):
        return "0x" + color[1:]
    return color


def _escape_drawtext(s: str, is_path: bool = False) -> str:
    r"""
    Экранирование для drawtext.
    Если is_path=True — сначала нормализуем слэши Windows (меняем \ на /).
    """
    if is_path:
        # FFmpeg на Windows отлично ест прямые слэши, а обратные ломают парсер.
        s = s.replace("\\", "/")

    # Двоеточие разделяет параметры фильтра
    s = s.replace(":", "\\:")
    # Апостроф закрывает строку в фильтре
    s = s.replace("'", "\\'")
    # Процент запускает подстановку %{...} в drawtext
    s = s.replace("%", "\\%")
    return s


def build_drawtext_filter(t: OverlayText) -> str:
    """
    Возвращает строку фильтра для FFmpeg (без префикса -vf).
    """
    # Передаём is_path=True, чтобы \ заменились на / перед экранированием
    font_path = _escape_drawtext(t.font_path, is_path=True)
    text = _escape_drawtext(t.text, is_path=False)

    filters = []

    band_height = t.font_size + 40
    band_y = f"h-{band_height}-{t.y_offset}"

    if t.box_enabled:
        box_color = _normalize_color(t.box_color)
        filters.append(
            f"drawbox=x=0:y={band_y}:w=iw:h={band_height}:"
            f"color={box_color}@{t.box_alpha}:t=fill"
        )
        text_y = f"{band_y}+({band_height}-th)/2"
    else:
        text_y = f"h-th-{t.y_offset}"

    x_expr = f"w-mod(t*{t.speed}\\,{max(1, t.repeat_period_sec)}*{t.speed})"

    font_color = _normalize_color(t.font_color)

    filters.append(
        f"drawtext=fontfile='{font_path}':text='{text}':fontsize={t.font_size}:"
        f"fontcolor={font_color}@{t.font_alpha}:"
        f"x={x_expr}:y={text_y}"
    )

    return ",".join(filters)