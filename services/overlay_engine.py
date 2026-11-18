"""
Движок Overlay-подсистемы (Smart Proxy).

Для каждого канала из рабочего плейлиста запускается HLS-прокси:
  - нет активного правила -> FFmpeg -c:v copy (0% CPU)
  - активно правило -> FFmpeg + drawtext (libx264)

Поток: /proxy/hls/<ch_id>/index.m3u8
"""
import hashlib
import os
import time
from datetime import datetime
from pathlib import Path

from .m3u_parser import parse_m3u
from .overlay_models import OverlayConfig, OverlayRule
from .overlay_filter import build_drawtext_filter, build_ticker_overlay_filter
from .ticker_render import has_emoji, render_ticker_png
from .encoders import overlay_video_args, qsv_available
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
        # Тексты тикеров: файл на канал + кэш последнего записанного текста
        self._text_dir = self.hls_dir.parent / "overlay_text"
        self._text_dir.mkdir(parents=True, exist_ok=True)
        self._text_cache: dict[str, str] = {}
        # PNG-тикеры для текстов с эмодзи (drawtext их не умеет)
        self._ticker_dir = self.hls_dir.parent / "ticker"
        self._ticker_dir.mkdir(parents=True, exist_ok=True)
        self._png_meta: dict[str, tuple[int, int]] = {}
        # Аппаратный энкод: включаем, только если h264_qsv реально работает
        self._use_qsv: bool = qsv_available()

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

        # ------------ подсказка -----------------
        # Прокси нужен только тем каналам, которые вообще упомянуты в правилах
        # (активных или нет). Остальным — не нужен: MPTS читает их напрямую.
        # Экономия: минус процесс, минус сегменты на диск, минус двойной трафик.
        needed_for_overlay = {
            u for rule in self.cfg.rules for u in rule.channel_urls if u in working
        }

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
            if url not in working or url not in needed_for_overlay:
                self._stop_channel(url)

        # 4. Запускаем то, что в рабочем плейлисте
        for url in self.cfg.working_channel_urls:
            if url not in needed_for_overlay:
                continue  # оверлея нет — пусть MPTS читает источник напрямую
            active_rule = active_rules_by_url.get(url)
            expected_rule_id = active_rule.id if active_rule else None

            current = alive_state.get(url)
            if current and current["rule_id"] == expected_rule_id:
                # Правило то же, но текст мог измениться — обновляем файл,
                # FFmpeg продолжает работать (эфир и MPTS не рвутся).
                if active_rule and self._text_cache.get(current["ch_id"]) != active_rule.text.text:
                    if has_emoji(active_rule.text.text):
                        # PNG не перечитывается на ходу — перезапускаем канал.
                        # MPTS это переживает: он читает HLS через отдельный сервер.
                        self._stop_channel(url)
                        current = None
                    else:
                        self._write_text(current["ch_id"], active_rule.text.text)
                        self._text_cache[current["ch_id"]] = active_rule.text.text
                        print(f"[overlay] {current['ch_id']} text updated live")
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

    def _text_path(self, ch_id: str) -> Path:
        return self._text_dir / f"{ch_id}.txt"

    def _ticker_png(self, ch_id: str) -> Path:
        return self._ticker_dir / f"{ch_id}.png"

    def _render_ticker(self, ch_id: str, rule: "OverlayRule"):
        """Рендерит текст правила в PNG с цветными эмодзи."""
        png = self._ticker_png(ch_id)
        text = rule.text
        try:
            w, h = render_ticker_png(
                text.text,
                png,
                font_path=getattr(text, "font_path", None),
                font_size=int(getattr(text, "font_size", 28)),
                font_color=getattr(text, "font_color", "#FFFFFF"),
            )
        except Exception as exc:
            print(f"[overlay] ticker render failed: {exc}")
            return png, 0, 0
        self._png_meta[ch_id] = (w, h)
        return png, w, h

    def _write_text(self, ch_id: str, text: str) -> Path:
        """Пишет текст тикера в файл. drawtext с reload=1 читает его на ходу,
        поэтому правка строки не перезапускает FFmpeg (и не рвёт MPTS)."""
        path = self._text_path(ch_id)
        clean = (text or "").replace("\r", " ").replace("\n", " ")
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(clean, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            try:
                path.write_text(clean, encoding="utf-8")
            except Exception as exc:
                print(f"[overlay] text write failed: {exc}")
        return path


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
            if has_emoji(rule.text.text):
                # Эмодзи: drawtext не умеет цветные глифы — накладываем PNG
                png, w, h = self._render_ticker(ch_id, rule)
                meta = self._png_meta.get(ch_id)
                if not meta or not png.exists():
                    png, w, h = self._render_ticker(ch_id, rule)
                    meta = (w, h)
                cmd += ["-loop", "1", "-i", str(png)]
                cmd += ["-filter_complex", build_ticker_overlay_filter(rule.text, meta[0]),
                        "-map", "[v]", "-map", "0:a?"]
            else:
                if not self._text_path(ch_id).exists():
                    # файл текста нужен drawtext уже на старте (reload=1)
                    self._write_text(ch_id, rule.text.text)
                drawtext = build_drawtext_filter(rule.text, text_file=str(self._text_path(ch_id)))
                cmd += ["-vf", drawtext]
            cmd += overlay_video_args(use_qsv=self._use_qsv)
        else:
            cmd += ["-c", "copy"]

        cmd += ["-c:a", "copy"]
        cmd += [
            "-f", "hls",
            "-hls_time", str(self.cfg.hls_time_sec),
            "-hls_list_size", "15",
            # ------------ подсказка ----------------
# Убрали +temp_file: на Windows rename(.tmp → .m3u8) не работает,
# пока Flask отдаёт index.m3u8 читателям (мозаика, MPTS).
# Windows блокирует rename поверх открытого файла.
# Без +temp_file FFmpeg пишет in-place: файл существует всегда,
# гонка длится микросекунды, MPTS/мозаика на HTTP переживают её.
            "-hls_flags", "delete_segments+append_list+discont_start",
            "-hls_segment_filename", str(out_dir / "seg_%03d.ts"),
            str(playlist),
        ]
        return cmd

    def _start_channel(self, url: str, rule: OverlayRule | None) -> None:
        ch_id = _channel_id(url)
        out_dir = self.hls_dir / f"proxy_{ch_id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        if rule:
            if has_emoji(rule.text.text):
                self._render_ticker(ch_id, rule)
            else:
                self._write_text(ch_id, rule.text.text)
            self._text_cache[ch_id] = rule.text.text

        cmd = self._build_command(url, rule)
        mode = f"OVERLAY({rule.name})" if rule else "COPY"
        proc_name = f"proxy_{ch_id}"

        res = self.pm.start(proc_name, cmd)

        # ------------ подсказка -----------------
        # QSV зависит от драйвера: если энкодер не поднялся — один раз
        # откатываемся на libx264 и больше не пробуем, чтобы не рвать эфир.
        if res.get("ok") and rule and self._use_qsv:
            time.sleep(0.8)
            if not self.pm.status(proc_name).get("running"):
                print("[overlay] h264_qsv не поднялся — перехожу на libx264")
                self._use_qsv = False
                self.pm.stop(proc_name)
                cmd = self._build_command(url, rule)
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