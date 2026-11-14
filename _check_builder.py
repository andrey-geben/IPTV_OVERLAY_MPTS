"""
Проверка ffmpeg_builder в изоляции.

Что делаем:
  1. Собираем MPTS-команду для двух каналов из твоего плейлиста.
  2. Печатаем её (чтобы ты видел, что получается).
  3. Запускаем через ProcessManager на 15 секунд с выводом в файл.
  4. Останавливаем, проверяем, что файл создан и не пустой.

ВАЖНО: файл-выход безопасен — ничего не отправляется в сеть.
Для реальной проверки на PBI потом поменяем output_url на udp://...
"""

import time

from config import Config
from services import ProcessManager, build_mpts_command, MptsChannel, MptsProfile


# Два канала из твоего плейлиста (для проверки хватит двух)
TEST_CHANNELS = [
    MptsChannel(name="Первый канал", url="http://10.24.0.151/Pervyi_kanal/index.m3u8"),
    MptsChannel(name="Россия 1",    url="http://10.24.0.151/Rossiia_1/index.m3u8"),
]

# Куда пишем тестовый MPTS
OUTPUT_FILE = Config.LOGS_DIR.parent / "test_mpts.ts"


def main() -> int:
    profile = MptsProfile(
        channels=TEST_CHANNELS,
        output_url=str(OUTPUT_FILE),
    )

    cmd = build_mpts_command(profile)
    print("=== СОБРАННАЯ КОМАНДА ===")
    print(" ".join(cmd))
    print()

    # Удалим старый файл, если был
    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()
        print(f"Удалён старый файл: {OUTPUT_FILE}")

    pm = ProcessManager(Config.LOGS_DIR)

    print("\n=== START ===")
    start_result = pm.start("test_mpts", cmd)
    print(start_result)
    if not start_result.get("ok"):
        return 1

    print("\nРаботаем 60 секунд...")
    time.sleep(60)

    print("\n=== STATUS (before stop) ===")
    print(pm.status("test_mpts"))

    print("\n=== STOP ===")
    print(pm.stop("test_mpts"))

    print("\n=== РЕЗУЛЬТАТ ===")
    if OUTPUT_FILE.exists():
        size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
        print(f"Файл создан: {OUTPUT_FILE}")
        print(f"Размер: {size_mb:.2f} MB")
        if size_mb < 0.1:
            print("⚠ Файл очень маленький — возможно, FFmpeg не смог прочитать входы.")
            print("  Загляни в лог: data/logs/test_mpts.log")
            return 2
    else:
        print("⚠ Файл НЕ создан. Смотри data/logs/test_mpts.log")
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())