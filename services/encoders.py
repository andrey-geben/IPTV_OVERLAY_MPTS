"""Выбор видеокодера для оверлея: аппаратный QSV с откатом на libx264.

h264_qsv на Intel iGPU стоит ~5 % CPU на канал против ~18 % у libx264,
но зависит от драйвера и числа одновременных сессий. Поэтому:
  * доступность проверяется один раз (ffmpeg -encoders);
  * если QSV-процесс не поднялся, движок откатывается на libx264 и больше
    не пытается — эфир не должен страдать из-за железа.
"""

from __future__ import annotations

import functools
import subprocess


@functools.lru_cache(maxsize=1)
def qsv_available() -> bool:
    """Есть ли в сборке FFmpeg аппаратный H.264-энкодер."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return "h264_qsv" in out
    except Exception:
        return False


def nvenc_available() -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return "h264_nvenc" in out
    except Exception:
        return False


def overlay_video_args(use_qsv: bool = True, bitrate: str = "2500k",
                       maxrate: str = "3000k", bufsize: str = "5000k",
                       gop: int = 50) -> list[str]:
    """Аргументы кодирования видео для канала с бегущей строкой.

    QSV: закрытый GOP каждые 2 с (g=50 при 25 fps), без B-кадров —
    чтобы HLS-клиенты (VLC, MPTS) переключались на границах keyframe.
    """
    if use_qsv and qsv_available():
        return [
            "-c:v", "h264_qsv",
            "-preset", "veryfast",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-g", str(gop),
            "-bf", "0",
            "-pix_fmt", "nv12",
        ]

    return [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", bitrate,
        "-maxrate", maxrate,
        "-bufsize", bufsize,
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
    ]
