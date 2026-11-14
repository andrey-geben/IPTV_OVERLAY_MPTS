"""
Движок Overlay-подсистемы (Smart Proxy).

Гарантирует, что для каждого канала из рабочего плейлиста
запущен стабильный HLS-прокси.
Накладывает бегущую строку через перезапуск (discont_start).
Потокобезопасен: все изменения active_channels защищены RLock.
"""

import hashlib
import threading
import time
from datetime import datetime
from pathlib import Path

from .m3u_parser import parse_m3u
from .overlay_models import OverlayConfig, OverlayRule
from .overlay_filter import build_drawtext_filter
from .process_manager import ProcessManager


def _channel_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:8]


class OverlayEngine:
    def __init__(self, pm: ProcessManager, hls_dir: Path, cfg: OverlayConfig):
        self.pm = pm
        self.hls_dir = Path(hls_dir)
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self._lock = threading.RLock()
        # {url: {"rule_id": str|None, "process_name": str, "ch_id": str}}
        self.active_channels: dict[str, dict] = {}

    # ---------- Публичный API ----------

    def get_active_snapshot(self) -> list[dict]:
        """Потокобезопасный снимок для Flask-маршрутов."""
        with self._lock:
            return [
                {"url": url, **info}
                for url, info in self.active_channels.items()
            ]

    def sync(self) -> None:
        """Сверяет рабочий плейлист с реальностью. Запускает/останавливает прокси."""
        now = datetime.now()

        # 1. Какие правила активны сейчас?
        active_rules_by_url: dict[str, OverlayRule] = {}
        for rule in self.cfg.rules:
            if not rule.enabled or not self._is_active(rule, now):
                continue
            for url in rule.channel_urls:
                if url not in active_rules_by_url or rule.priority > active_rules_by_url[url].priority:
                    active_rules_by_url[url] = rule

        # 2. Потокобезопасно снимаем текущее состояние
        with self._lock:
            current_state = dict(self.active_channels)
            working = set(self.cfg.working_channel_urls)

        # 3. Останавливаем то, что больше не в рабочем плейлисте
        for url in current_state.keys():
            if url not in working:
                self._stop_channel(url)

        # 4. Запускаем/обновляем то, что в рабочем плейлисте
        for url in self.cfg.working_channel_urls:
            active_rule = active_rules_by_url.get(url)
            expected_rule_id = active_rule.id if active_rule else None

            current = current_state.get(url)
            if current and current["rule_id"] == expected_rule_id:
                continue  # уже работает как надо

            if current:
                self._stop_channel(url)

            self._start_channel(url, active_rule)

        # 5. Чистим старые файлы
        self._cleanup_old_segments(max_age_sec=3600)

    def stop_all(self) -> None:
        with self._lock:
            urls = list(self.active_channels.keys())
        for url in urls:
            self._stop_channel(url)

    def get_playlist(self, original_m3u: str) -> str:
        channels = parse_m3u(original_m3u)
        lines = ["#EXTM3U"]
        working_set = set(self.cfg.working_channel_urls)

        with self._lock:
            active_ids = {v["ch_id"] for v in self.active_channels.values()}

        for ch in channels:
            ch_url = ch["url"]
            ch_id = _channel_id(ch_url)

            if ch_url in working_set:
                new_url = f"{self.cfg.public_base_url}/proxy/hls/{ch_id}/index.m3u8"
            else:
                new_url = ch_url

            lines.append(
                f'#EXTINF:-1 group-title="{ch["group"]}" '
                f'tvg-id="{ch["tvg_id"]}",{ch["name"]}'
            )
            lines.append(new_url)

        return "\n".join(lines)

    # ---------- Внутренние методы ----------

    def _is_active(self, rule: OverlayRule, now: datetime) -> bool:
        # Проверка дат
        if rule.start_date or rule.end_date:
            if rule.is_yearly:
                now_md = now.strftime("%m-%d")
                start_md = rule.start_date[5:] if rule.start_date else "01-01"
                end_md = rule.end_date[5:] if rule.end_date else "12-31"
                if start_md <= end_md:
                    if not (start_md <= now_md <= end_md):
                        return False
                else:
                    if not (now_md >= start_md or now_md <= end_md):
                        return False
            else:
                now_str = now.strftime("%Y-%m-%d")
                if rule.start_date and now_str < rule.start_date:
                    return False
                if rule.end_date and now_str > rule.end_date:
                    return False

        # Проверка дней недели и времени
        w = rule.window
        if w.days and now.weekday() not in w.days:
            return False
        current = now.strftime("%H:%M")
        if w.start_hhmm <= w.end_hhmm:
            return w.start_hhmm <= current <= w.end_hhmm
        return current >= w.start_hhmm or current <= w.end_hhmm

    def _start_channel(self, url: str, rule: OverlayRule | None) -> None:
        ch_id = _channel_id(url)
        out_dir = self.hls_dir / f"proxy_{ch_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        playlist = out_dir / "index.m3u8"

        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", url,
        ]

        if rule:
            drawtext = build_drawtext_filter(rule.text)
            cmd += ["-vf", drawtext]
            cmd += [
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "5000k",
            ]
            mode = f"OVERLAY({rule.name})"
        else:
            cmd += ["-c", "copy"]
            mode = "COPY"

        cmd += ["-c:a", "copy"]
        cmd += [
            "-f", "hls",
            "-hls_time", str(self.cfg.hls_time_sec),
            "-hls_list_size", "15",
            "-hls_flags", "delete_segments+append_list+discont_start",
            "-hls_segment_filename", str(out_dir / "seg_%03d.ts"),
            str(playlist),
        ]

        proc_name = f"proxy_{ch_id}"
        res = self.pm.start(proc_name, cmd)

        if res.get("ok"):
            with self._lock:
                self.active_channels[url] = {
                    "rule_id": rule.id if rule else None,
                    "process_name": proc_name,
                    "ch_id": ch_id,
                }
            print(f"[overlay] {ch_id} started in mode: {mode}")
        else:
            print(f"[overlay] Failed to start {ch_id}: {res.get('error')}")

    def _stop_channel(self, url: str) -> None:
        with self._lock:
            info = self.active_channels.pop(url, None)
        if info:
            self.pm.stop(info["process_name"])
            print(f"[overlay] {info['ch_id']} stopped")

    def _cleanup_old_segments(self, max_age_sec: int = 3600) -> None:
        now = time.time()
        try:
            for ch_id_dir in self.hls_dir.iterdir():
                if not ch_id_dir.is_dir() or not ch_id_dir.name.startswith("proxy_"):
                    continue
                for f in ch_id_dir.glob("*.ts"):
                    try:
                        if now - f.stat().st_mtime > max_age_sec:
                            f.unlink(missing_ok=True)
                    except (PermissionError, FileNotFoundError):
                        pass
        except FileNotFoundError:
            pass