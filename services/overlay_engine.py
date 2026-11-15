"""
Движок Overlay-подсистемы (Smart Proxy).

Для каждого канала из рабочего плейлиста запускается HLS-прокси:
  - нет активного правила -> FFmpeg -c:v copy (0% CPU)
  - активно правило -> FFmpeg + drawtext (libx264)

Поток: /proxy/hls/<ch_id>/index.m3u8
"""
import hashlib
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
        # {url: {"rule_id": str|None, "process_name": str, "ch_id": str}}
        self.active_channels: dict[str, dict] = {}

    # ---------- Public API ----------

    def sync(self) -> None:
        """Сверяет рабочий плейлист с реальностью. Запускает/останавливает прокси."""
        now = datetime.now()
        print(f"[sync] CALLED. working={len(self.cfg.working_channel_urls)}, "
              f"rules={len(self.cfg.rules)}, active={len(self.active_channels)}")

        # -----подсказка------------
        # Набор URL, которые должны работать. Именно его проверяет
        # строка `if url not in working:` в шаге 3.
        working = set(self.cfg.working_channel_urls)

        # 1. Активные правила
        active_rules_by_url: dict[str, OverlayRule] = {}
        for rule in self.cfg.rules:
            if not rule.enabled or not self._is_active(rule, now):
                continue
            for url in rule.channel_urls:
                if url not in active_rules_by_url or rule.priority > active_rules_by_url[url].priority:
                    active_rules_by_url[url] = rule

        # 2. Проверяем, какие процессы реально живы
        alive_state: dict[str, dict] = {}
        for url, info in list(self.active_channels.items()):
            if self.pm.status(info["process_name"]).get("running"):
                alive_state[url] = info
            else:
                print(f"[overlay] {info['ch_id']} died — cleaning")
                # --- ПОДСКАЗКА: Читаем последнюю строку лога, чтобы понять причину падения ---
                try:
                    log_path = self.pm.logs_dir / f"{info['process_name']}.log"
                    if log_path.exists():
                        lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                        if lines:
                            print(f"[overlay] LAST LOG LINE: {lines[-1]}")
                except Exception:
                    pass
                # ------------------------------------------------------------------------------
                del self.active_channels[url]

        # 3. Останавливаем то, что выпало из рабочего плейлиста
        for url in list(alive_state.keys()):
            if url not in working:
                self._stop_channel(url)

        # 4. Запускаем то, что в рабочем плейлисте
        for url in self.cfg.working_channel_urls:
            active_rule = active_rules_by_url.get(url)
            expected_rule_id = active_rule.id if active_rule else None

            current = alive_state.get(url)
            if current and current["rule_id"] == expected_rule_id:
                continue  # уже в нужном режиме

            # Если процесс с этим ch_id уже запущен и режим совпадает — просто регистрируем
            if self._process_matches_state(url, active_rule):
                ch_id = _channel_id(url)
                self.active_channels[url] = {
                    "rule_id": expected_rule_id,
                    "process_name": f"proxy_{ch_id}",
                    "ch_id": ch_id,
                }
                print(f"[overlay] {ch_id} restored in mode: {'OVERLAY' if active_rule else 'COPY'}")
                continue

            # Стоп старый, если был
            if current:
                self._stop_channel(url)

            print(f"[sync] starting {url} (rule={expected_rule_id})")
            self._start_channel(url, active_rule)

        # 5. Чистка старых сегментов
        self._cleanup_old_segments(max_age_sec=3600)

    def stop_all(self) -> None:
        for url in list(self.active_channels.keys()):
            self._stop_channel(url)

    def get_active_snapshot(self) -> list[dict]:
        return [{"url": url, **info} for url, info in self.active_channels.items()]

    # ---------- Internal ----------

    def _is_active(self, rule: OverlayRule, now: datetime) -> bool:
        if rule.start_date or rule.end_date:
            if rule.is_yearly:
                now_md = now.strftime("%m-%d")
                start_md = rule.start_date[5:] if rule.start_date else "01-01"
                end_md = rule.end_date[5:] if rule.end_date else "12-31"
                if start_md <= end_md:
                    if not (start_md <= now_md <= end_md): return False
                else:
                    if not (now_md >= start_md or now_md <= end_md): return False
            else:
                now_str = now.strftime("%Y-%m-%d")
                if rule.start_date and now_str < rule.start_date: return False
                if rule.end_date and now_str > rule.end_date: return False
        w = rule.window
        if w.days and now.weekday() not in w.days: return False
        current = now.strftime("%H:%M")
        if w.start_hhmm <= w.end_hhmm:
            return w.start_hhmm <= current <= w.end_hhmm
        return current >= w.start_hhmm or current <= w.end_hhmm

    def _process_matches_state(self, url: str, rule: OverlayRule | None) -> bool:
        """Проверяет: жив ли процесс и совпадает ли его команда с ожидаемой."""
        ch_id = _channel_id(url)
        proc_name = f"proxy_{ch_id}"
        saved_cmd = self.pm.get_command(proc_name)
        if saved_cmd is None:
            return False
        if not self.pm.status(proc_name).get("running"):
            return False
        expected = self._build_command(url, rule)
        return saved_cmd == expected

    def _build_command(self, url: str, rule: OverlayRule | None) -> list[str]:
        ch_id = _channel_id(url)
        out_dir = self.hls_dir / f"proxy_{ch_id}"
        playlist = out_dir / "index.m3u8"

        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",

            # -----подсказка------------
            # Блок против "non-existing PPS 0 referenced".
            # 1) Даём FFmpeg до 30 секунд на анализ входа — успевает
            #    найти keyframe с SPS/PPS, даже если сегменты короткие.
            # 2) Битые пакеты выбрасываем, PTS генерируем, декодер не падает.
            # 3) -y — перезаписывать index.m3u8 без вопросов (с -nostdin
            #    FFmpeg иначе не может спросить и валится).
            "-analyzeduration", "30000000",
            "-probesize", "30000000",
            "-fflags", "+discardcorrupt+genpts",
            "-err_detect", "ignore_err",
            "-y",

            # Переподключение (было у тебя)
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",

            "-i", url,
        ]

        if rule:
            drawtext = build_drawtext_filter(rule.text)
            cmd += ["-vf", drawtext]
            cmd += [
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "5000k",
                # -----подсказка------------
                # Закрытый GOP каждые 2 сек (50 кадров при 25 fps) — HLS-клиенты
                # (VLC, ffplay, MPTS) переключаются на границах keyframe'ов.
                # Без этого libx264 ставит keyframe'ы по сцене, и HLS «дёргается».
                "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
            ]
        else:
            cmd += ["-c", "copy"]

        cmd += ["-c:a", "copy"]
        cmd += [
            "-f", "hls",
            "-hls_time", str(self.cfg.hls_time_sec),
            "-hls_list_size", "15",
            # -----подсказка------------
# +temp_file — писать index.m3u8 через .tmp → атомарный rename.
# Без него FFmpeg открывает index.m3u8 на запись напрямую (truncate→write),
# и в это окно файл либо 0 байт, либо залочен. MPTS открывает вход один
# раз, ловит окно → "No such file or directory". С +temp_file файл
# существует всегда — заменяется атомарно.
            "-hls_flags", "delete_segments+append_list+discont_start+temp_file",
            "-hls_segment_filename", str(out_dir / "seg_%03d.ts"),
            str(playlist),
        ]
        return cmd

    def _start_channel(self, url: str, rule: OverlayRule | None) -> None:
        ch_id = _channel_id(url)
        out_dir = self.hls_dir / f"proxy_{ch_id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = self._build_command(url, rule)
        mode = f"OVERLAY({rule.name})" if rule else "COPY"
        proc_name = f"proxy_{ch_id}"

        res = self.pm.start(proc_name, cmd)
        if res.get("ok"):
            self.active_channels[url] = {
                "rule_id": rule.id if rule else None,
                "process_name": proc_name,
                "ch_id": ch_id,
            }
            print(f"[overlay] {ch_id} started in mode: {mode}")
        else:
            print(f"[overlay] FAILED to start {ch_id}: {res.get('error')}")

    def _stop_channel(self, url: str) -> None:
        info = self.active_channels.pop(url, None)
        if info:
            self.pm.stop(info["process_name"])
            print(f"[overlay] {info['ch_id']} stopped")

    def _cleanup_old_segments(self, max_age_sec: int = 3600) -> None:
        now = time.time()
        try:
            for d in self.hls_dir.iterdir():
                if not d.is_dir() or not d.name.startswith("proxy_"):
                    continue
                for f in d.glob("*.ts"):
                    try:
                        if now - f.stat().st_mtime > max_age_sec:
                            f.unlink(missing_ok=True)
                    except (PermissionError, FileNotFoundError):
                        pass
        except FileNotFoundError:
            pass