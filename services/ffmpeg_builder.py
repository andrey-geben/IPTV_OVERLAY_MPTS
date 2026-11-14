"""
Сборщик команд FFmpeg для MPTS-мультиплексирования.

Логика повторяет build_command() из mpts-maker (проверено на живом PBI),
но вынесена в чистый сервис: принимает профиль — возвращает список args.

ВАЖНО: этот модуль НЕ запускает FFmpeg. Только собирает команду.
Запуск — через ProcessManager.
"""

from dataclasses import dataclass, field


@dataclass
class MptsChannel:
    """Один канал в будущем MPTS."""
    name: str          # человекочитаемое имя (используется в SDT service_name)
    url: str           # URL входного потока (HLS, UDP, HTTP-TS)


@dataclass
class MptsProfile:
    """Профиль MPTS: набор каналов + параметры выхода."""
    channels: list[MptsChannel] = field(default_factory=list)
    output_url: str = ""
    use_channel_names: bool = True       # если True — service_name = channel.name,
                                         # если False — fallback на Channel_N
    local_addr: str = ""                 # НОВОЕ: IP-адрес исходящего интерфейса


def build_mpts_command(profile: MptsProfile, ffmpeg_path: str = "ffmpeg") -> list[str]:
    """Собирает список аргументов для запуска ffmpeg-мультиплексора."""
    if not profile.channels:
        raise ValueError("MPTS-профиль не содержит каналов")

    if not profile.output_url:
        raise ValueError("Не задан output_url (куда отправлять MPTS)")

    # ---------- Общие флаги ----------
    cmd: list[str] = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-progress", "pipe:1",
        "-fflags", "+genpts+igndts",
        "-avoid_negative_ts", "make_zero",
    ]

    # ---------- Входы ----------
    for ch in profile.channels:
        # Reconnect-флаги ТОЛЬКО для прямых HTTP-потоков (HTTP-TS).
        # Для HLS они вредны — FFmpeg зациклится на переподключениях.
        if ch.url.startswith(("http://", "https://")):
            cmd += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ]
        cmd += ["-i", ch.url]

    # ---------- Программы и маппинг ----------
    for i, ch in enumerate(profile.channels):
        # Имя сервиса для SDT. Если use_channel_names — берём из плейлиста.
        if profile.use_channel_names and ch.name:
            # В title= нельзя включать ':' — он разделяет параметры программы.
            # Заменяем на пробел, если встретится.
            service_title = ch.name.replace(":", " ")
        else:
            service_title = f"Channel_{i + 1}"

        cmd += [
            "-program", f"title={service_title}:st={i * 2}:st={i * 2 + 1}",
            "-map", f"{i}:v?",
            "-map", f"{i}:a?",
        ]

    # ---------- Выход ----------
    cmd += ["-c", "copy"]
    cmd += ["-bsf:v", "dump_extra=freq=keyframe"]
    cmd += ["-mpegts_flags", "+resend_headers+initial_discontinuity"]
    cmd += ["-muxdelay", "0.1", "-muxpreload", "0.1"]
    cmd += ["-f", "mpegts", profile.output_url]

    return cmd


def build_udp_url(host: str, port: int, pkt_size: int = 1316, local_addr: str = "", ttl: int = 1) -> str:
    """
    Собирает UDP-URL для FFmpeg с привязкой к сетевому интерфейсу.
    local_addr — исходный IP (например, 192.168.1.10).
    ttl — время жизни пакета (для multicast).
    """
    url = f"udp://{host}:{port}?pkt_size={pkt_size}&ttl={ttl}"
    if local_addr:
        url += f"&localaddr={local_addr}"
    return url