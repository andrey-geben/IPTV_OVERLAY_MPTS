#!/usr/bin/env python
"""Ingest канала: папка с видео → библиотека одного формата.

Пример:
    python tools/ingest_channel.py ^
        --src "D:\\Media\\Channel1" ^
        --dst "data\\channels\\ch1" ^
        --width 720 --height 576 --fps 25 --bitrate 3000k --jobs 2

    # с логотипом
    python tools/ingest_channel.py --src ... --dst ... --logo static/logo.png --logo-pos topright

После ingest файлы можно склеивать без перекодирования (`-c copy`),
поэтому эфир «папка = канал» почти не тратит CPU.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.ingest import IngestProfile, ingest_folder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Нормализация медиатеки канала")
    ap.add_argument("--src", required=True, help="папка с исходными видеофайлами")
    ap.add_argument("--dst", required=True, help="куда складывать готовые .ts")
    ap.add_argument("--width", type=int, default=720)
    ap.add_argument("--height", type=int, default=576)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--bitrate", default="3000k")
    ap.add_argument("--audio-rate", type=int, default=48000)
    ap.add_argument("--audio-bitrate", default="128k")
    ap.add_argument("--gop", type=int, default=50)
    ap.add_argument("--jobs", type=int, default=2, help="сколько файлов кодировать параллельно")
    ap.add_argument("--logo", default=None, help="PNG-логотип (вшивается в видео)")
    ap.add_argument("--logo-pos", default="topright",
                    choices=["topright", "topleft", "bottomright", "bottomleft"])
    ap.add_argument("--logo-scale", type=float, default=0.12, help="доля ширины кадра")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_dir():
        print(f"Нет такой папки: {src}")
        return 1

    profile = IngestProfile(
        width=args.width, height=args.height, fps=args.fps,
        bitrate=args.bitrate, audio_rate=args.audio_rate,
        audio_bitrate=args.audio_bitrate, gop=args.gop,
        logo=args.logo, logo_pos=args.logo_pos, logo_scale=args.logo_scale,
    )

    result = ingest_folder(src, args.dst, profile, jobs=args.jobs)
    if result.failed:
        print("\nНе удалось обработать:")
        for f in result.failed:
            print(f"  {f['file']}: {f['error'][:200]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
