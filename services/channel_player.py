"""Каналы «папка = канал»: хранилище и воспроизведение.

Канал — это папка с видеофайлами. После ingest все файлы приведены к одному
профилю, поэтому эфир идёт через `-c copy` (без перекодирования) и почти не
тратит CPU. Порядок показа перемешивается заново на каждой итерации.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .ingest import IngestProfile, collect_files, ingest_folder


@dataclass
class Channel:
    id: str
    name: str
    source_dir: str
    output_dir: str
    profile: dict = field(default_factory=lambda: asdict(IngestProfile()))
    enabled: bool = False
    ingested: int = 0
    last_ingest: float = 0.0
    ingest_state: str = "idle"       # idle | running | error
    ingest_error: str = ""


class ChannelStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.channels: list[Channel] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        for item in data.get("channels", []):
            try:
                self.channels.append(Channel(**item))
            except TypeError:
                continue

    def save(self) -> None:
        with self._lock:
            payload = {"channels": [asdict(c) for c in self.channels]}
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, ch_id: str) -> Channel | None:
        return next((c for c in self.channels if c.id == ch_id), None)

    def add(self, name: str, source_dir: str, profile: dict | None = None) -> Channel:
        ch_id = f"chan-{random.getrandbits(24):06x}"
        ch = Channel(
            id=ch_id, name=name or Path(source_dir).name,
            source_dir=str(source_dir),
            output_dir=str(Path("data") / "channels" / ch_id),
            profile=profile or asdict(IngestProfile()),
        )
        self.channels.append(ch)
        self.save()
        return ch

    def remove(self, ch_id: str) -> bool:
        ch = self.get(ch_id)
        if not ch:
            return False
        self.channels.remove(ch)
        self.save()
        return True


class ChannelPlayer:
    """Запускает канал как HLS-поток и перемешивает порядок на каждой итерации."""

    def __init__(self, pm, hls_dir: Path | str) -> None:
        self.pm = pm
        self.hls_dir = Path(hls_dir)
        self.lists_dir = self.hls_dir.parent / "channels"
        self.lists_dir.mkdir(parents=True, exist_ok=True)
        self._threads: dict[str, threading.Thread] = {}

    # ---------- плейлист ----------
    def list_path(self, ch: Channel) -> Path:
        return self.lists_dir / f"{ch.id}.txt"

    def build_playlist(self, ch: Channel) -> tuple[Path, float]:
        """Перемешивает файлы и пишет concat-список. Возвращает (путь, длительность)."""
        out_dir = Path(ch.output_dir)
        manifest_path = out_dir / "manifest.json"
        entries: list[dict] = []
        if manifest_path.exists():
            try:
                entries = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
            except Exception:
                entries = []
        entries = [e for e in entries if (out_dir / e.get("out", "")).exists()]
        if not entries:
            raise RuntimeError("Нет готовых файлов: сначала выполните ingest")

        random.shuffle(entries)
        total = sum(float(e.get("duration") or 0) for e in entries) or 60.0
        # ВАЖНО: пути в concat-списке абсолютные — ffmpeg ищет файлы
        # относительно каталога списка, а не рабочего каталога процесса.
        lines = [
            "file '{}'".format((out_dir / e["out"]).resolve().as_posix())
            for e in entries
        ]
        path = self.list_path(ch)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path, total

    # ---------- воспроизведение ----------
    def start(self, ch: Channel) -> dict:
        name = f"channel_{ch.id}"
        if self.pm.status(name).get("running"):
            return {"ok": False, "error": "Канал уже запущен"}

        try:
            playlist, total = self.build_playlist(ch)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        out_dir = self.hls_dir / f"proxy_{ch.id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-stream_loop", "-1", "-re",
            "-f", "concat", "-safe", "0", "-i", str(playlist),
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "10",
            "-hls_flags", "delete_segments+append_list+discont_start",
            "-hls_segment_filename", str(out_dir / "seg_%03d.ts"),
            str(out_dir / "index.m3u8"),
        ]
        res = self.pm.start(name, cmd)
        if not res.get("ok"):
            return res

        # перемешивание порядка на каждой итерации
        stop = threading.Event()
        self._threads[ch.id] = threading.Thread(
            target=self._reshuffle_loop, args=(ch, total, stop),
            daemon=True, name=f"ch-{ch.id}",
        )
        self._threads[ch.id].stop_event = stop  # type: ignore[attr-defined]
        self._threads[ch.id].start()
        return {"ok": True, "pid": res.get("pid")}

    def _reshuffle_loop(self, ch: Channel, iteration_sec: float, stop: threading.Event) -> None:
        while not stop.wait(max(20.0, iteration_sec)):
            try:
                self.build_playlist(ch)
                print(f"[channel] {ch.id}: новый порядок на следующую итерацию")
            except Exception as exc:
                print(f"[channel] {ch.id}: не удалось перемешать: {exc}")

    def stop(self, ch: Channel) -> dict:
        th = self._threads.pop(ch.id, None)
        if th is not None:
            ev = getattr(th, "stop_event", None)
            if ev is not None:
                ev.set()
        return self.pm.stop(f"channel_{ch.id}")

    def status(self, ch: Channel) -> dict:
        st = self.pm.status(f"channel_{ch.id}")
        out_dir = self.hls_dir / f"proxy_{ch.id}"
        playlist = out_dir / "index.m3u8"
        ready = playlist.exists() and (time.time() - playlist.stat().st_mtime) < 10
        return {
            "running": bool(st.get("running")),
            "pid": st.get("pid"),
            "uptime_sec": st.get("uptime_sec", 0),
            "hls_url": f"/proxy/hls/{ch.id}/index.m3u8",
            "ready": ready,
        }

    # ---------- ingest ----------
    def start_ingest(self, ch: Channel) -> dict:
        if ch.ingest_state == "running":
            return {"ok": False, "error": "Ingest уже идёт"}
        ch.ingest_state = "running"
        ch.ingest_error = ""

        def run():
            try:
                profile = IngestProfile(**ch.profile)
                res = ingest_folder(ch.source_dir, ch.output_dir, profile, jobs=2,
                                    progress=lambda m: print(f"[ingest {ch.id}] {m}"))
                ch.ingested = len([f for f in Path(ch.output_dir).glob("*.ts")])
                ch.last_ingest = time.time()
                ch.ingest_state = "error" if res.failed else "idle"
                ch.ingest_error = res.failed[0]["error"][:300] if res.failed else ""
            except Exception as exc:
                ch.ingest_state = "error"
                ch.ingest_error = str(exc)[:300]
                print(f"[ingest {ch.id}] failed: {exc}")

        threading.Thread(target=run, daemon=True, name=f"ingest-{ch.id}").start()
        return {"ok": True}
