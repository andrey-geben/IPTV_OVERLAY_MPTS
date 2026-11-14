"""
Проверка символов палитры в DejaVu Sans.
Создаёт ОДИН контактный лист (PNG) со всеми символами + кодами Unicode.
"""

import subprocess
from pathlib import Path

OUT_DIR = Path("data/test_symbols")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "contact_sheet_dejavu.png"

# Кандидаты — где может лежать DejaVuSans.ttf
FONT_CANDIDATES = [
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/dejavu/DejaVuSans.ttf",
    str(Path(__file__).resolve().parent / "static" / "fonts" / "DejaVuSans.ttf"),
    str(Path.home() / "AppData/Local/Microsoft/Windows/Fonts/DejaVuSans.ttf"),
]


def find_font() -> str | None:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


# Все символы палитры — по одному экземпляру
SYMBOLS = [
    "★","☆","✪","✦","✧","✶","✷","✸","✹","✵","✴","✳","✲","✱","✰",
    "♥","♡","♦","♢","♣","♧","♠","♤",
    "♪","♫","♬","♩","♭","♮","♯",
    "→","←","↑","↓","↔","↕","⇒","⇐","⇑","⇓","⇔","➜","➤","➔",
    "✓","✔","✗","✘","☑","☒","☐",
    "☀","☼","☁","☂","☃","❄","❅","❆","☔",
    "●","○","◆","◇","■","□","▲","△","▼","▽","◀","▶","◄","►","◯","◉","◎",
    "§","¶","†","‡","•","‰","°","±","×","÷","©","®","™","☎","☏","☺","☻","♨","☯","✂","✈",
]


def escape_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def build_contact_sheet(font_path: str) -> None:
    cols = 12
    cell_w = 130
    cell_h = 130
    rows = (len(SYMBOLS) + cols - 1) // cols
    width = cols * cell_w
    height = rows * cell_h

    font_esc = escape_drawtext(font_path)

    filters = []
    for idx, ch in enumerate(SYMBOLS):
        col = idx % cols
        row = idx // cols
        x = col * cell_w + 20
        y = row * cell_h + 10
        ch_esc = escape_drawtext(ch)

        # Сам символ
        filters.append(
            f"drawtext=fontfile='{font_esc}':text='{ch_esc}':"
            f"fontsize=64:fontcolor=white:x={x}:y={y}"
        )
        # Код U+XXXX мелким шрифтом
        code = f"U+{ord(ch):04X}"
        filters.append(
            f"drawtext=fontfile='{font_esc}':text='{code}':"
            f"fontsize=14:fontcolor=gray:x={x}:y={y+82}"
        )

    vf = ",".join(filters)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=1",
        "-vf", vf, "-frames:v", "1",
        str(OUT_FILE),
    ]

    print(f"Генерирую контактный лист ({len(SYMBOLS)} символов, {cols}×{rows})...")
    subprocess.run(cmd, check=True, timeout=60)


def main() -> None:
    font = find_font()
    if not font:
        print("❌ DejaVuSans.ttf не найден. Проверил пути:")
        for p in FONT_CANDIDATES:
            mark = "✓" if Path(p).exists() else "✗"
            print(f"   {mark} {p}")
        print()
        print("Куда положить шрифт:")
        print(f"   {Path(__file__).resolve().parent / 'static' / 'fonts' / 'DejaVuSans.ttf'}")
        return

    print(f"✅ Шрифт найден: {font}")
    build_contact_sheet(font)
    print()
    print("=" * 60)
    print(f"Готово! Открой: {OUT_FILE}")
    print()
    print("Что искать в картинке:")
    print("   ✅ Символ виден — работает")
    print("   ❌ Квадратик / пусто — не работает")
    print()
    print("Пришли мне:")
    print("   1) Скриншот (или сам PNG)")
    print("   2) Или просто список номеров ячеек с квадратиками")


if __name__ == "__main__":
    main()