"""
Сборщик фильтров FFmpeg для overlay (drawbox + drawtext).

Изолирован от моделей и движка. Принимает OverlayText — возвращает строку
для -vf (video filter). Никакой логики процессов здесь нет.
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


def _escape_drawtext(s: str) -> str:
    r"""
    Экранирование для drawtext.
    В FFmpeg drawtext спецсимволы: \ : ' %
    А для Windows-путей ещё и сам \ (обратный слэш).
    """
    # Сначала экранируем обратный слэш (важно для путей Windows)
    s = s.replace("\\", "\\\\")
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

    Логика:
      - Если box_enabled=True, рисуем СПЛОШНУЮ полосу через весь экран
        (drawbox) и пускаем текст поверх неё (drawtext).
      - Если box_enabled=False, просто текст с отступом от низа.

    Формула движения текста:
      x = w - mod(t*speed, period*speed)   — плавное движение справа налево
      y = <рассчитывается в зависимости от режима>
    """
    font_path = _escape_drawtext(t.font_path)
    text = _escape_drawtext(t.text)

    filters = []

    # Высота полосы: размер шрифта + отступы сверху/снизу
    band_height = t.font_size + 40
    band_y = f"h-{band_height}-{t.y_offset}"

    if t.box_enabled:
        # 1. Сплошная полоса от края до края
        box_color = _normalize_color(t.box_color)
        filters.append(
            f"drawbox=x=0:y={band_y}:w=iw:h={band_height}:"
            f"color={box_color}@{t.box_alpha}:t=fill"
        )
        # 2. Текст поверх полосы, выровнен по центру по вертикали
        # th — встроенная переменная FFmpeg (высота текста)
        text_y = f"{band_y}+({band_height}-th)/2"
    else:
        # Если полоса выключена — просто текст с отступом от низа
        text_y = f"h-th-{t.y_offset}"

    # Формула движения: справа налево
    x_expr = f"w-mod(t*{t.speed}\\,{max(1, t.repeat_period_sec)}*{t.speed})"

    font_color = _normalize_color(t.font_color)

    filters.append(
        f"drawtext=fontfile='{font_path}':text='{text}':fontsize={t.font_size}:"
        f"fontcolor={font_color}@{t.font_alpha}:"
        f"x={x_expr}:y={text_y}"
    )

    return ",".join(filters)