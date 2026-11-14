"""
Быстрая проверка ProcessManager в изоляции от Flask.
Запускается так:  python _check_process.py

Скрипт:
  1. Запускает тестовый FFmpeg-генератор (testsrc).
  2. Через 2 секунды запрашивает статус (должен быть running=True, PID, CPU).
  3. Останавливает процесс.
  4. Ещё раз проверяет статус (должен быть running=False).

Если FFmpeg не установлен в PATH — увидишь ошибку "Команда не найдена",
это сигнал проверить `ffmpeg -version` в терминале.
"""

import sys
import time

from config import Config
from services import ProcessManager


# Тестовая команда: генератор цветных полос, отдаёт в «никуда» (null).
# -re    — читать в реальном времени (медленно, как живой поток)
# -f null -  — вывод отбрасывается, ничего не занимает
TEST_COMMAND = [
    "ffmpeg",
    "-re",
    "-f", "lavfi",
    "-i", "testsrc=size=320x240:rate=10",
    "-t", "3600",
    "-f", "null",
    "-",
]


def main() -> int:
    pm = ProcessManager(Config.LOGS_DIR)

    print("=== START ===")
    start_result = pm.start("test_ffmpeg", TEST_COMMAND)
    print(start_result)
    if not start_result.get("ok"):
        print("\nНе удалось запустить процесс. Проверь, установлен ли FFmpeg:")
        print("  ffmpeg -version")
        return 1

    time.sleep(2)

    print("\n=== STATUS (after 2s) ===")
    print(pm.status("test_ffmpeg"))

    print("\n=== STOP ===")
    print(pm.stop("test_ffmpeg"))

    time.sleep(0.5)

    print("\n=== STATUS AFTER STOP ===")
    print(pm.status("test_ffmpeg"))

    return 0


if __name__ == "__main__":
    sys.exit(main())