"""
Проверка, какие символы рендерятся в разных шрифтах Windows.
Генерирует тестовое видео для каждого шрифта.
"""

import subprocess
from pathlib import Path

OUT_DIR = Path("data/test_symbols")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Все символы из палитры (по 1 экземпляру)
ALL_SYMBOLS = (
    "★☆✪✦✧✶✷✸✹✵✴✳✲✱✰"
    "♥♡♦♢♣♧♠♤"
    "♪♫♬♩♭♮♯"
    "→←↑↓↔↕⇒⇐⇑⇓⇔➜➤➔"
    "✓✔✗✘☑☒☐"
    "☀☼☁☂☃❄❅❆☔"
    "●○◆◇■□▲△▼▽◀▶◄►◯◉◎"
    "§¶†‡•‰°±×÷©®™☎☏☺☻♨☯✂✈"
)

# Текст для проверки кириллицы
TEXT = f"Привет КТВ! {ALL_SYMBOLS}"

# Шрифты для теста
FONTS = [
    ("Arial", "C:/Windows/Fonts/arial.ttf"),
    ("Segoe UI", "C:/Windows/Fonts/segoeui.ttf"),
    ("Segoe UI Symbol", "C:/Windows/Fonts/seguisym.ttf"),
    ("Calibri", "C:/Windows/Fonts/calibri.ttf"),
    ("Tahoma", "C:/Windows/Fonts/tahoma.ttf"),
    ("Verdana", "C:/Windows/Fonts/verdana.ttf"),
]


def escape_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def make_test(name: str, font_path: str) -> Path | None:
    if not Path(font_path).exists():
        print(f"❌ Шрифт не найден: {font_path}")
        return None

    out_file = OUT_DIR / f"symbols_{name.replace(' ', '_')}.mp4"
    font_esc = escape_drawtext(font_path)
    text_esc = escape_drawtext(TEXT)

    # Маленький шрифт, чтобы все символы влезли в кадр
    vf = (
        f"drawtext=fontfile='{font_esc}':"
        f"text='{text_esc}':"
        f"fontsize=24:"
        f"fontcolor=white:"
        f"x=20:y=(h-text_h)/2:"
        f"line_spacing=10"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=1920x300:d=2:r=5",
        "-vf", vf, "-t", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(out_file),
    ]

    print(f"Генерирую {name}...")
    try:
        subprocess.run(cmd, check=True, timeout=30)
        print(f"  ✅ {out_file.name}")
        return out_file
    except subprocess.CalledProcessError as exc:
        print(f"  ❌ Ошибка: {exc}")
        return None


def main() -> None:
    print("=" * 60)
    print("Тест символов в разных шрифтах")
    print("=" * 60)
    print()
    print(f"Текст ({len(TEXT)} символов):")
    print(f"  {TEXT[:60]}...")
    print()

    results = []
    for name, path in FONTS:
        out = make_test(name, path)
        if out:
            results.append((name, out))

    print()
    print("=" * 60)
    print("Готово! Открой файлы в VLC:")
    for name, out in results:
        print(f"  • {out}  ({name})")
    print()
    print("В каком файле БОЛЬШЕ ВСЕГО символов видно (не квадратиков)?")
    print("Тот шрифт и поставим дефолтом для overlay.")


if __name__ == "__main__":
    main()