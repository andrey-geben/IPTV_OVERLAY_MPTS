"""
Проверка ChannelProxy и ZMQ.
Запускает прокси, ждёт 5 секунд (чтобы HLS создался),
включает overlay, потом выключает.
Запускай ffplay / VLC на HLS-ссылку параллельно.
"""

import time
from config import Config
from services import ProcessManager, ChannelProxy

TEST_URL = "http://10.24.0.151/Pervyi_kanal/index.m3u8"

def main():
    pm = ProcessManager(Config.LOGS_DIR)
    proxy = ChannelProxy(pm, Config.HLS_DIR)

    print("1. Запуск Proxy...")
    hls_url = proxy.ensure_proxy(TEST_URL)
    print(f"   HLS-ссылка: http://127.0.0.1:5000{hls_url}")

    print("2. Ждём 5 секунд (FFmpeg создаёт первые сегменты)...")
    time.sleep(5)

    print("\n>>> ОТКРОЙ В VLC / ffplay: http://127.0.0.1:5000" + hls_url)
    print(">>> Там сейчас идёт чистая картинка без строки.")
    input(">>> Нажми Enter, чтобы ВКЛЮЧИТЬ бегущую строку...\n")

    print("3. Отправка ZMQ-команды: включить overlay...")
    proxy.update_overlay(TEST_URL, "Тест горячей замены!", enabled=True)
    print("   Готово. Смотри в плеер.")

    input(">>> Нажми Enter, чтобы ВЫКЛЮЧИТЬ бегущую строку...\n")

    print("4. Отправка ZMQ-команды: выключить overlay...")
    proxy.update_overlay(TEST_URL, "", enabled=False)
    print("   Готово. Смотри в плеер.")

    input(">>> Нажми Enter для остановки...\n")
    pm.stop(f"proxy_{hls_url.split('/')[3]}")

if __name__ == "__main__":
    main()