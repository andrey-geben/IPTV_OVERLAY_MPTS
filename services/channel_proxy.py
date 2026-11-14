"""
ChannelProxy — Origin Server для каналов.

Держит один постоянный FFmpeg-процесс на канал.
Создаёт стабильный HLS. Управляет overlay через AZMQ (без перезапуска).
"""

import hashlib
import time
from pathlib import Path

import zmq

from .process_manager import ProcessManager


def _channel_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:8]


def _get_zmq_port(ch_id: str) -> int:
    return 15000 + (int(ch_id[:4], 16) % 1000)


class ChannelProxy:
    def __init__(self, pm: ProcessManager, hls_dir: Path):
        self.pm = pm
        self.hls_dir = Path(hls_dir)
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        self.active_proxies: dict[str, dict] = {}

    def ensure_proxy(self, url: str) -> str:
        ch_id = _channel_id(url)
        if ch_id in self.active_proxies:
            proc_name = self.active_proxies[ch_id]["process_name"]
            if self.pm.status(proc_name).get("running"):
                return f"/proxy/hls/{ch_id}/index.m3u8"
            del self.active_proxies[ch_id]
        return self._start_proxy(url, ch_id)

    def _start_proxy(self, url: str, ch_id: str) -> str:
        out_dir = self.hls_dir / f"proxy_{ch_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        playlist = out_dir / "index.m3u8"
        port = _get_zmq_port(ch_id)

        # ФИЛЬТР:
        #   azmq   — принимает ZMQ-команды (а НЕ zmq, который отправляет кадры)
        #   drawbox — полоса, выключена (enable=0)
        #   drawtext — текст, выключен
        #
        # ВАЖНО: fontfile с Windows-путём экранируется правильно для фильтра.
        vf = (
            f"azmq=bind_address=tcp\\://127.0.0.1\\:{port},"
            f"drawbox=x=0:y=h-126:w=iw:h=76:color=black@0.7:t=fill:enable=0,"
            f"drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text=' ':"
            f"fontsize=36:fontcolor=white:x=w-mod(t*100\\,6000):"
            f"y=h-126+(76-th)/2:enable=0"
        )

        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", url,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "5000k",
            "-c:a", "copy",
            "-f", "hls",
            "-hls_time", "2", "-hls_list_size", "5",
            "-hls_flags", "delete_segments+append_list+discont_start",
            "-hls_segment_filename", str(out_dir / "seg_%03d.ts"),
            str(playlist),
        ]

        proc_name = f"proxy_{ch_id}"
        res = self.pm.start(proc_name, cmd)
        if not res.get("ok"):
            raise RuntimeError(f"Не удалось запустить proxy: {res.get('error')}")

        print(f"[proxy] Started {ch_id} on AZMQ port {port}")

        self.active_proxies[ch_id] = {
            "url": url,
            "process_name": proc_name,
            "port": port,
        }
        return f"/proxy/hls/{ch_id}/index.m3u8"

    def update_overlay(self, url: str, text: str, enabled: bool,
                       speed: int = 100, y_offset: int = 50):
        """
        Обновляет состояние overlay без перезапуска FFmpeg.
        Отправляет команды через AZMQ (ZMQ_PAIR).
        """
        ch_id = _channel_id(url)
        if ch_id not in self.active_proxies:
            print(f"[proxy] {ch_id} not active, skip ZMQ")
            return

        port = self.active_proxies[ch_id]["port"]
        context = zmq.Context()
        sock = context.socket(zmq.PAIR)
        # LINGER=0 — при close() не ждём доставки (иначе Python виснет,
        # если FFmpeg уже умер или не читает)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.SNDTIMEO, 1000)  # 1 сек на отправку
        try:
            sock.connect(f"tcp://127.0.0.1:{port}")
            time.sleep(0.1)

            safe_text = text.replace("'", "\\'").replace(":", "\\:")
            try:
                sock.send_string(f"Parsed_drawtext_0 text '{safe_text}'", zmq.NOBLOCK)
                sock.send_string(f"Parsed_drawtext_0 enable {1 if enabled else 0}", zmq.NOBLOCK)
                sock.send_string(f"Parsed_drawbox_0 enable {1 if enabled else 0}", zmq.NOBLOCK)
                print(f"[proxy] ZMQ sent to {ch_id}: enabled={enabled}, text='{safe_text[:30]}'")
            except zmq.Again:
                print(f"[proxy] ZMQ send timeout for {ch_id} (FFmpeg busy or dead)")
        except Exception as e:
            print(f"[proxy] ZMQ error for {ch_id}: {e}")
        finally:
            sock.close()
            context.term()