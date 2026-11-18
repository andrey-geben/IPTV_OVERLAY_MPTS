"""Ingest: подготовка медиатеки канала к вещанию.

Идея «папка = канал»:
  1) ingest — один раз приводим все файлы папки к ОДНОМУ профилю
     (кодек, разрешение, fps, GOP, аудио) и, если нужно, вшиваем логотип;
  2) эфир — склейка готовых файлов через `-c copy`, то есть без перекодирования
     (≈0 % CPU) и с перемешиванием порядка на каждой итерации.

Почему так: транскод «на лету» для 40–50 каналов на одном CPU невозможен,
а copy-воспроизведение подготовленной библиотеки — почти бесплатно.

Модуль собирает команды и запускает их. Логика эфира — отдельный этап.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .encoders import qsv_available

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".ts", ".m2ts", ".mts",
    ".webm", ".flv", ".wmv", ".mpg", ".mpeg", ".m4v",
}


@dataclass
class IngestProfile:
    """Целевой профиль канала. Все файлы станут одинаковыми по параметрам."""
    width: int = 720
    height: int = 576
    fps: int = 25
    bitrate: str = "3000k"
    audio_rate: int = 48000
    audio_bitrate: str = "128k"
    gop: int = 50                 # 2 секунды при 25 fps — границы для HLS/MPTS
    logo: str | None = None       # PNG с прозрачностью
    logo_pos: str = "topright"    # topright / topleft / bottomright / bottomleft
    logo_scale: float = 0.12      # доля ширины кадра
    logo_margin: int = 20


@dataclass
class IngestResult:
    ok: int = 0
    skipped: int = 0
    failed: list[dict] = field(default_factory=list)
    total_sec: float = 0.0
    output_dir: str = ""


def collect_files(folder: Path | str) -> list[Path]:
    folder = Path(folder)
    files = [p for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    return files


def _probe(path: Path) -> dict:
    """Длительность и наличие звука — от этого зависит команда."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout
        data = json.loads(out or "{}")
    except Exception:
        return {"duration": 0.0, "has_audio": False}
    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        pass
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return {"duration": duration, "has_audio": has_audio}


def _logo_xy(pos: str, margin: int) -> tuple[str, str]:
    pos = (pos or "topright").lower()
    if pos == "topleft":
        return str(margin), str(margin)
    if pos == "bottomleft":
        return str(margin), f"main_h-overlay_h-{margin}"
    if pos == "bottomright":
        return f"main_w-overlay_w-{margin}", f"main_h-overlay_h-{margin}"
    return f"main_w-overlay_w-{margin}", str(margin)


def build_ingest_command(src: Path | str, dst: Path | str, profile: IngestProfile,
                         has_audio: bool = True, use_qsv: bool | None = None) -> list[str]:
    """Команда нормализации одного файла в общий MPEG-TS профиль."""
    src, dst = str(src), str(dst)
    if use_qsv is None:
        use_qsv = qsv_available()

    # --- входы: сначала все -i, потом фильтры (иначе ffmpeg ругается) ---
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", src]
    next_idx = 1
    audio_idx = 0 if has_audio else None
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r={profile.audio_rate}:cl=stereo"]
        audio_idx = next_idx
        next_idx += 1
    logo_idx = None
    if profile.logo:
        cmd += ["-i", str(profile.logo)]
        logo_idx = next_idx
        next_idx += 1

    base = (
        f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease,"
        f"pad={profile.width}:{profile.height}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={profile.fps}"
    )

    if logo_idx is not None:
        x, y = _logo_xy(profile.logo_pos, profile.logo_margin)
        w = int(profile.width * max(0.03, min(0.4, profile.logo_scale)))
        filter_complex = (
            f"[0:v]{base}[base];"
            f"[{logo_idx}:v]scale={w}:-1[logo];"
            f"[base][logo]overlay={x}:{y}:format=auto[v]"
        )
        cmd += ["-filter_complex", filter_complex, "-map", "[v]"]
    else:
        cmd += ["-vf", base, "-map", "0:v:0"]

    cmd += ["-map", f"{audio_idx}:a:0"]
    if not has_audio:
        cmd += ["-shortest"]          # тишина бесконечная — режем по видео

    cmd += ["-c:a", "aac", "-ar", str(profile.audio_rate),
            "-ac", "2", "-b:a", profile.audio_bitrate]

    if use_qsv:
        cmd += ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", profile.bitrate,
                "-maxrate", profile.bitrate, "-bufsize", "1500k",
                "-g", str(profile.gop), "-bf", "0", "-pix_fmt", "nv12"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-b:v", profile.bitrate,
                "-maxrate", profile.bitrate, "-bufsize", "1500k",
                "-g", str(profile.gop), "-keyint_min", str(profile.gop),
                "-sc_threshold", "0", "-pix_fmt", "yuv420p"]

    cmd += ["-f", "mpegts", "-mpegts_flags", "+resend_headers", dst]
    return cmd


def ingest_folder(src_dir: Path | str, dst_dir: Path | str, profile: IngestProfile,
                  jobs: int = 2, manifest_name: str = "manifest.json",
                  progress=print) -> IngestResult:
    """Нормализует папку целиком. Уже обработанные файлы пропускает."""
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    files = collect_files(src_dir)
    result = IngestResult(output_dir=str(dst_dir))
    t0 = time.time()

    manifest_path = dst_dir / manifest_name
    manifest = {"profile": asdict(profile), "files": []}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.setdefault("files", [])
        except Exception:
            pass
    done = {f.get("src") for f in manifest["files"]}

    todo = []
    for i, f in enumerate(files):
        rel = f.relative_to(src_dir)
        out_name = f"{i:04d}_{rel.stem[:40]}.ts".replace(" ", "_")
        out = dst_dir / out_name
        if str(f) in done and out.exists():
            result.skipped += 1
            continue
        todo.append((f, out))

    if not todo:
        progress(f"Нечего обрабатывать: {result.skipped} файлов уже готовы.")
        result.total_sec = round(time.time() - t0, 1)
        return result

    progress(f"Файлов к обработке: {len(todo)} (пропущено {result.skipped}), "
             f"потоков: {jobs}, профиль {profile.width}x{profile.height}@{profile.fps}")

    def work(item):
        f, out = item
        info = _probe(f)
        cmd = build_ingest_command(f, out, profile, has_audio=info["has_audio"])
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if res.returncode != 0:
            return {"file": str(f), "error": (res.stderr or "").strip()[-400:]}
        return {"file": str(f), "out": str(out), "duration": info["duration"],
                "size": out.stat().st_size if out.exists() else 0}

    with ThreadPoolExecutor(max_workers=max(1, int(jobs))) as pool:
        for n, item in enumerate(pool.map(work, todo), 1):
            if "error" in item:
                result.failed.append(item)
                progress(f"  [{n}/{len(todo)}] ОШИБКА: {Path(item['file']).name}")
            else:
                result.ok += 1
                out = Path(item["out"])
                manifest["files"] = [x for x in manifest["files"] if x.get("src") != item["file"]]
                manifest["files"].append({
                    "src": item["file"], "out": out.name,
                    "duration": round(item["duration"], 2), "size": item["size"],
                })
                progress(f"  [{n}/{len(todo)}] {out.name} — {item['duration']:.1f} с")

    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, manifest_path)

    result.total_sec = round(time.time() - t0, 1)
    progress(f"Готово: ок {result.ok}, ошибок {len(result.failed)}, "
             f"пропущено {result.skipped}, {result.total_sec} с")
    return result
