#!/usr/bin/env python
"""Разбивка каналов на несколько MPTS-групп.

Зачем: один MPTS на 40–50 программ — это 130–160 Мбит/с, что не влезает
в один QAM-канал КТВ (~48 Мбит/с). Стандартно головная станция принимает
несколько MPTS по 8–10 программ (25–32 Мбит/с), каждый на свою
multicast-группу и свой вход PBI.

Примеры:
    # посмотреть, что получится (ничего не пишет)
    python tools/split_mpts_groups.py --per-group 10 --start-ip 239.0.0.11

    # применить к конфигу (создаст бэкап mpts_config.json.bak-split)
    python tools/split_mpts_groups.py --per-group 10 --start-ip 239.0.0.11 --apply

    # взять каналы только одной группы и включить автозапуск
    python tools/split_mpts_groups.py --per-group 10 --from-group grp-7ae656 --auto-start --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from services import MptsGroup, MptsStorage  # noqa: E402


def _split(seq: list, size: int) -> list[list]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Разбить каналы на MPTS-группы")
    ap.add_argument("--per-group", type=int, default=10,
                    help="сколько программ в одном MPTS (по умолчанию 10)")
    ap.add_argument("--start-ip", default="239.0.0.11",
                    help="первый multicast-адрес (по умолчанию 239.0.0.11)")
    ap.add_argument("--port", type=int, default=5000, help="UDP-порт (по умолчанию 5000)")
    ap.add_argument("--from-group", default=None,
                    help="взять каналы из этой группы (иначе — объединение всех групп)")
    ap.add_argument("--local-addr", default=None,
                    help="исходящий IP (по умолчанию — как в первой группе)")
    ap.add_argument("--auto-start", action="store_true",
                    help="включить автозапуск у новых групп")
    ap.add_argument("--apply", action="store_true", help="записать изменения в конфиг")
    ap.add_argument("--config", default=None, help="путь к mpts_config.json (для тестов)")
    args = ap.parse_args()

    cfg_path = args.config or Config.MPTS_CONFIG_PATH
    storage = MptsStorage(cfg_path)
    cfg = storage.load()

    if not cfg.groups:
        print("В конфиге нет ни одной MPTS-группы — сначала создайте её в панели.")
        return 1

    if args.from_group:
        src = next((g for g in cfg.groups if g.id == args.from_group), None)
        if not src:
            print(f"Группа {args.from_group} не найдена. Есть: "
                  f"{', '.join(g.id for g in cfg.groups)}")
            return 1
        urls = list(src.channel_urls)
    else:
        seen, urls = set(), []
        for g in cfg.groups:
            for u in g.channel_urls:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)

    if not urls:
        print("Не выбрано ни одного канала.")
        return 1

    local_addr = args.local_addr
    if local_addr is None:
        local_addr = next((g.local_addr for g in cfg.groups if g.local_addr), "")

    if args.per_group * 3_200_000 > 48_000_000:
        print(f"⚠ {args.per_group} программ — это ~{args.per_group * 3.2:.0f} Мбит/с, "
              f"больше ёмкости одного QAM-канала (~48 Мбит/с).")

    base_ip = args.start_ip.rsplit(".", 1)
    if len(base_ip) != 2:
        print("--start-ip должен быть полным IPv4-адресом, например 239.0.0.11")
        return 1
    prefix, last = base_ip[0], int(base_ip[1])

    chunks = _split(urls, args.per_group)
    groups: list[MptsGroup] = []
    for i, chunk in enumerate(chunks):
        groups.append(MptsGroup(
            id=f"grp-{uuid.uuid4().hex[:6]}",
            name=f"MPTS {i + 1} ({len(chunk)} кан.)",
            host=f"{prefix}.{last + i}",
            port=args.port,
            local_addr=local_addr or "",
            channel_urls=chunk,
            auto_start=args.auto_start,
        ))

    total_mbps = len(urls) * 3.2
    print(f"Каналов: {len(urls)} → групп: {len(groups)} по {args.per_group}")
    print(f"Суммарный битрейт: ~{total_mbps:.0f} Мбит/с "
          f"(на группу ~{args.per_group * 3.2:.0f} Мбит/с)\n")
    for g in groups:
        print(f"  {g.host}:{g.port}  {g.name}  "
              f"({len(g.channel_urls)} программ, ~{len(g.channel_urls) * 3.2:.0f} Мбит/с)")

    if not args.apply:
        print("\nЭто предпросмотр. Для записи добавьте --apply")
        return 0

    backup = Path(str(cfg_path) + ".bak-split")
    shutil.copy2(cfg_path, backup)
    cfg.groups = groups
    storage.save(cfg)
    print(f"\nГотово. Бэкап: {backup}")
    print("Проверьте в панели /mpts, что каналы распределены верно, и запустите группы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
