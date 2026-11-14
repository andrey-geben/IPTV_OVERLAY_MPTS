"""
Проверка рендеринга эмодзи в FFmpeg drawtext.

Генерирует 3 тестовых видео с эмодзи в разных шрифтах.
Результат в data/test_emoji/. Открой в VLC и сравни.
"""

import subprocess
from pathlib import Path

OUT_DIR = Path("data/test_emoji")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Тестовый текст с эмодзи и кириллицей
TEXT = "Привет! 🎄 🎉 🇷🇺 ⚡ 😊"

# Шрифты для проверки (проверь существование в C:/Windows/Fonts/)
FONTS = [
    ("Arial", "C:/Windows/Fonts/arial.ttf"),
    ("Segoe UI Emoji", "C:/Windows/Fonts/seguiemj.ttf"),
    ("Segoe UI Symbol", "C:/Windows/Fonts/seguisym.ttf"),
]


def escape_drawtext(s: str) -> str:
    """Экранирование для drawtext."""
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    s = s.replace("%", "\\%")
    return s


def make_test(name: str, font_path: str) -> Path:
    out_file = OUT_DIR / f"emoji_{name.replace(' ', '_')}.mp4"

    if not Path(font_path).exists():
        print(f"❌ Шрифт не найден: {font_path}")
        return None

    font_esc = escape_drawtext(font_path)
    text_esc = escape_drawtext(TEXT)

    # Фон — синий, чтобы белый текст и чёрные глифы эмодзи были видны
    vf = (
        f"drawtext=fontfile='{font_esc}':"
        f"text='{text_esc}':"
        f"fontsize=48:"
        f"fontcolor=white:"
        f"box=1:"
        f"boxcolor=blue@0.8:"
        f"boxborderw=20:"
        f"x=(w-text_w)/2:"
        f"y=(h-text_h)/2"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=3:r=25",
        "-vf", vf,
        "-t", "3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(out_file),
    ]

    print(f"Генерирую {name}...")
    try:
        subprocess.run(cmd, check=True, timeout=30)
        size = out_file.stat().st_size
        print(f"  ✅ {out_file.name} ({size // 1024} KB)")
        return out_file
    except subprocess.CalledProcessError as exc:
        print(f"  ❌ Ошибка FFmpeg: {exc}")
        return None


def main() -> None:
    print("=" * 60)
    print("Тест эмодзи в FFmpeg drawtext")
    print("=" * 60)
    print()
    print(f"Текст: {TEXT}")
    print()

    results = []
    for name, font_path in FONTS:
        out = make_test(name, font_path)
        if out:
            results.append((name, out))

    print()
    print("=" * 60)
    print("Готово! Открой в VLC:")
    for name, out in results:
        print(f"  • {out}  ({name})")
    print()
    print("Сравни:")
    print("  - Эмодзи цветные? (обычно нет — FFmpeg их не поддерживает)")
    print("  - Эмодзи чёрно-белые контуром? (работает)")
    print("  - Квадратики/пропуски? (шрифт не содержит эмодзи)")
    print("  - Кириллица рендерится корректно? (важно!)")


if __name__ == "__main__":
    main()