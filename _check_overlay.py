"""
Быстрая проверка сборки drawtext-фильтра и сериализации правил.
Запуск: python _check_overlay.py

Ничего не запускает в FFmpeg — просто смотрит, что генерируется.
"""

from config import Config
from services import (
    OverlayText, TimeWindow, OverlayRule, OverlayConfig,
    OverlayStorage, build_drawtext_filter,
)


def main() -> None:
    print("=== 1. drawtext-фильтр (простой текст) ===")
    t1 = OverlayText(text="Внимание, профилактика!", font_size=40, y_offset=60)
    print(build_drawtext_filter(t1))
    print()

    print("=== 2. drawtext-фильтр (сложный текст с ':' и '%') ===")
    t2 = OverlayText(text="Скидка 50%: только сегодня!", font_color="yellow")
    print(build_drawtext_filter(t2))
    print()

    print("=== 3. Сохранение/загрузка правил ===")
    storage = OverlayStorage(Config.LOGS_DIR.parent / "overlay_config.json")

    cfg = OverlayConfig(
        rules=[
            OverlayRule(
                id="rule-1",
                name="Новогодняя",
                text=OverlayText(text="С Новым годом!", font_color="gold"),
                window=TimeWindow(days=[5, 6], start_hhmm="10:00", end_hhmm="22:00"),
                channel_urls=[
                    "http://10.24.0.151/Pervyi_kanal/index.m3u8",
                    "http://10.24.0.151/Rossiia_1/index.m3u8",
                ],
                priority=10,
            ),
        ],
        public_base_url="http://127.0.0.1:5000",
    )

    storage.save(cfg)
    loaded = storage.load()
    print(f"Правил в конфиге: {len(loaded.rules)}")
    for r in loaded.rules:
        print(f"  - {r.id}: {r.name} | {r.text.text} | каналов: {len(r.channel_urls)}")
    print(f"Файл: {storage.path}")


if __name__ == "__main__":
    main()