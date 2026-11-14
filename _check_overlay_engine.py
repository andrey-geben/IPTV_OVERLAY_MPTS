"""
Проверка OverlayEngine в изоляции.
Запускает прокси для одного канала на 15 секунд,
проверяет, что HLS-сегменты создались, и генерирует итоговый M3U.
"""

import time

from config import Config
from services import (
    ProcessManager, OverlayConfig, OverlayRule,
    OverlayText, TimeWindow,
)
from services.overlay_engine import OverlayEngine


TEST_URL = "http://10.24.0.151/Pervyi_kanal/index.m3u8"

# Создаём правило, активное прямо сейчас
now_start = "00:00"
now_end = "23:59"

def main() -> None:
    pm = ProcessManager(Config.LOGS_DIR)
    hls_dir = Config.LOGS_DIR.parent / "hls_test"

    cfg = OverlayConfig(
        hls_time_sec=2,
        hls_list_size=5,
        public_base_url="http://127.0.0.1:5000",
        rules=[
            OverlayRule(
                id="test-rule-1",
                name="Тестовая строка",
                text=OverlayText(text="ТЕСТ БЕГУЩЕЙ СТРОКИ", font_size=48, speed=150),
                window=TimeWindow(days=[], start_hhmm=now_start, end_hhmm=now_end),
                channel_urls=[TEST_URL],
            )
        ]
    )

    engine = OverlayEngine(pm, hls_dir, cfg)

    print("=== SYNC (запуск процессов) ===")
    engine.sync()
    print(f"Активных каналов: {len(engine.active_channels)}")
    for url, info in engine.active_channels.items():
        print(f"  {url} -> процесс {info['process_name']} (ch_id={info['ch_id']})")

    print("\nЖдём 15 секунд (FFmpeg создаёт HLS-сегменты)...")
    time.sleep(15)

    print("\n=== ПРОВЕРКА HLS ===")
    if not engine.active_channels:
        print("❌ Ни один процесс не запустился. Смотри data/logs/")
        return

    for url, info in engine.active_channels.items():
        ch_id = info["ch_id"]
        out_dir = hls_dir / ch_id
        if not out_dir.exists():
            print(f"❌ Папка {out_dir} не создана")
            continue

        ts_files = list(out_dir.glob("*.ts"))
        playlist = out_dir / "index.m3u8"
        print(f"Папка: {out_dir}")
        print(f"  Сегментов .ts: {len(ts_files)}")
        print(f"  index.m3u8: {'✅ есть' if playlist.exists() else '❌ нет'}")

    print("\n=== ГЕНЕРАЦИЯ ИТОГОВОГО M3U ===")
    original_m3u = f"""#EXTM3U
#EXTINF:-1 group-title="Эфирные" tvg-id="2",Первый канал
{TEST_URL}
#EXTINF:-1 group-title="Эфирные" tvg-id="3",Россия 1
http://10.24.0.151/Rossiia_1/index.m3u8
"""
    result_m3u = engine.get_playlist(original_m3u)
    print(result_m3u)

    print("\n=== STOP ALL ===")
    engine.stop_all()
    print("Готово.")


if __name__ == "__main__":
    main()