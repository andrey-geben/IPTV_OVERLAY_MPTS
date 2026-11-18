"""
Сборщик фильтров FFmpeg для overlay.
drawbox (полоса) + drawtext (бегущий текст), синхронизированные по enable.
"""

from .overlay_models import OverlayText


def _normalize_color(color: str) -> str:
    color = (color or "").strip()
    if not color:
        return "white"
    if color.startswith("#"):
        return "0x" + color[1:]
    return color


def _to_hex_color(color: str) -> str:
    color = (color or "black").strip().lower()
    named = {
        "black": "0x000000", "white": "0xFFFFFF", "red": "0xFF0000",
        "green": "0x00FF00", "blue": "0x0000FF", "yellow": "0xFFFF00"
    }
    if color in named:
        return named[color]
    if color.startswith("#"):
        return "0x" + color[1:].upper()
    if color.startswith("0x"):
        return color.upper()
    return "0x000000"


def _escape_drawtext(s: str, is_path: bool = False) -> str:
    if is_path:
        s = s.replace("\\", "/")
    s = s.replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    return s


def build_ticker_overlay_filter(t: OverlayText, png_width: int) -> str:
    """filter_complex для наложения PNG-строки (нужен для цветных эмодзи).

    [0:v] — видео канала, [1:v] — картинка строки (вход -loop 1 -i file.png).
    Возвращает граф, который заканчивается меткой [v].
    """
    band_height = t.font_size + 4
    y_offset = max(0, min(300, int(t.y_offset)))
    band_y = f"ih-{band_height}-{y_offset}"
    period = max(1, t.repeat_period_sec)
    speed = max(1, t.speed)
    on_screen = (720 + max(1, png_width)) / speed
    enable = f"lt(mod(t,{period}),{on_screen:.1f})"

    box = ""
    if t.box_enabled and float(t.box_alpha) > 0:
        box = (
            f"drawbox=x=0:y={band_y}:w=iw:h={band_height}:"
            f"color={_to_hex_color(t.box_color)}@{float(t.box_alpha)}:"
            f"thickness={band_height}:enable='{enable}'[bg];"
        )
        base = "[bg]"
        graph = f"[0:v]{box}"
    else:
        base = "[0:v]"
        graph = ""

    x_expr = f"w-mod(t*{speed}\\,{period}*{speed})"
    overlay = (
        f"{base}[1:v]overlay=x='{x_expr}':"
        f"y=main_h-{band_height}-{y_offset}+({band_height}-h)/2:enable='{enable}'[v]"
    )
    return graph + overlay


def build_drawtext_filter(t: OverlayText, text_file: str | None = None) -> str:
    """text_file — путь к txt-файлу тикера (drawtext reload=1).

    С файлом команда FFmpeg перестаёт зависеть от текста: правка строки
    не требует перезапуска процесса, значит HLS и MPTS не рвутся.
    """
    font_path = _escape_drawtext(t.font_path, is_path=True)
    if text_file:
        text_part = f"textfile='{_escape_drawtext(str(text_file), is_path=True)}':reload=1"
    else:
        text_part = f"text='{_escape_drawtext(t.text, is_path=False)}'"

    filters = []

    # -----подсказка------------
    # ВЫСОТА ПОЛОСЫ = размер шрифта + 4 px (по 2 px сверху и снизу).
    band_height = t.font_size + 4
    y_offset = max(0, min(300, int(t.y_offset)))

    # -----подсказка------------
    # ВНИМАНИЕ! В фильтре drawbox переменная 'h' означает ВЫСОТУ САМОГО
    # ПРЯМОУГОЛЬНИКА, а НЕ высоту кадра. Высота кадра в drawbox — это 'ih'.
    # Если написать y=h-40-50, FFmpeg посчитает 40-40-50 = -50 и нарисует
    # полосу за верхним краем экрана (её не будет видно).
    band_y_box = f"ih-{band_height}-{y_offset}"

    # -----подсказка------------
    # В фильтре drawtext переменная 'h' — это высота кадра. Поэтому для
    # текста используем её, чтобы центрировать его по вертикали.
    band_y_text = f"h-{band_height}-{y_offset}"

    period = max(1, t.repeat_period_sec)
    text_width_approx = t.font_size * len(t.text) * 0.6
    on_screen_sec = (720 + text_width_approx) / max(1, t.speed)
    enable_expr = f"lt(mod(t,{period}),{on_screen_sec:.1f})"

    alpha = max(0.0, min(1.0, float(t.box_alpha)))
    if t.box_enabled and alpha > 0:
        box_color = _to_hex_color(t.box_color)

        # -----подсказка------------
        # thickness={band_height} гарантированно работает во всех сборках
        # FFmpeg. Параметр 'fill' есть только в новых версиях.
        filters.append(
            f"drawbox=x=0:y={band_y_box}:w=iw:h={band_height}:"
            f"color={box_color}@{alpha}:thickness={band_height}:enable='{enable_expr}'"
        )

        text_y = f"{band_y_text}+({band_height}-th)/2"
    else:
        text_y = f"h-th-{y_offset}"

    x_expr = f"w-mod(t*{t.speed}\\,{period}*{t.speed})"
    font_color = _normalize_color(t.font_color)

    filters.append(
        f"drawtext=fontfile='{font_path}':{text_part}:fontsize={t.font_size}:"
        f"fontcolor={font_color}@{t.font_alpha}:x={x_expr}:y={text_y}"
    )
    return ",".join(filters)