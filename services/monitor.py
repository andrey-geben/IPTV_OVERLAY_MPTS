"""
Мониторинг и проверка доступности потоков.
"""

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil


def check_channel_alive(url: str, timeout: int = 12) -> dict:
    """Проверяет доступность канала через ffprobe (для входящих HLS/UDP)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height",
             "-of", "default=nw=1", "-i", url],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0 and "codec_name" in result.stdout:
            info = {}
            for line in result.stdout.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k.strip()] = v.strip()
            return {
                "url": url, "alive": True,
                "codec": info.get("codec_name", "?"),
                "resolution": f"{info.get('width', '?')}x{info.get('height', '?')}",
            }
        err = ""
        if result.stderr:
            lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
            err = lines[-1] if lines else "ffprobe: пустой вывод"
        return {"url": url, "alive": False, "error": err or "неизвестная ошибка"}
    except subprocess.TimeoutExpired:
        return {"url": url, "alive": False, "error": f"Таймаут {timeout}с"}
    except FileNotFoundError:
        return {"url": url, "alive": False, "error": "ffprobe не найден"}
    except Exception as exc:
        return {"url": url, "alive": False, "error": str(exc)}


def check_channels_parallel(urls: list[str], timeout: int = 12) -> list[dict]:
    if not urls:
        return []
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        futures = [pool.submit(check_channel_alive, u, timeout) for u in urls]
        for fut in futures:
            results.append(fut.result())
    order = {u: i for i, u in enumerate(urls)}
    results.sort(key=lambda r: order.get(r["url"], 999))
    return results


def validate_udp_stream(host: str, port: int, timeout_sec: int = 8) -> dict:
    """
    Читает UDP-поток через ffprobe (с таймаутом).
    Для multicast использует @ перед адресом.
    Возвращает:
      - alive: bool
      - programs: list[{"index": int, "name": str, "streams": [str, ...]}]
      - bitrate: str ("6554 kb/s") или "—"
      - error: str (если не удалось)
    """
    # Для multicast нужен @, для unicast — обычный адрес
    if host.startswith(("224.", "225.", "226.", "227.", "228.", "229.",
                        "230.", "231.", "232.", "233.", "234.", "235.",
                        "236.", "237.", "238.", "239.")):
        url = f"udp://@{host}:{port}?timeout={timeout_sec * 1000000}"
    else:
        url = f"udp://{host}:{port}?timeout={timeout_sec * 1000000}"

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_programs", "-show_entries",
             "program=program_id:program_tags=service_name:stream=codec_type,codec_name",
             "-of", "json", "-read_intervals", "%+3",
             "-i", url],
            capture_output=True, text=True, timeout=timeout_sec + 4,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"alive": False, "error": f"ffprobe не ответил за {timeout_sec + 4}с"}
    except FileNotFoundError:
        return {"alive": False, "error": "ffprobe не найден"}
    except Exception as exc:
        return {"alive": False, "error": str(exc)}

    if result.returncode != 0 or not result.stdout.strip():
        err_lines = [l for l in result.stderr.splitlines() if l.strip()]
        err = err_lines[-1] if err_lines else "Не удалось прочитать поток"
        return {"alive": False, "error": err}

    # Парсим JSON
    try:
        import json
        data = json.loads(result.stdout)
    except Exception:
        return {"alive": False, "error": "Невалидный JSON от ffprobe"}

    programs_raw = data.get("programs", [])
    programs = []
    for p in programs_raw:
        streams = []
        for s in p.get("streams", []):
            codec = s.get("codec_name", "?")
            kind = s.get("codec_type", "?")
            streams.append(f"{kind}:{codec}")
        programs.append({
            "index": p.get("program_id", "?"),
            "name": p.get("tags", {}).get("service_name", "?"),
            "streams": streams,
        })

    if not programs:
        return {"alive": False, "error": "Программы не обнаружены (MPTS пуст?)"}

    return {
        "alive": True,
        "programs": programs,
        "bitrate": "—",  # ffprobe на UDP не всегда показывает битрейт
        "url": url,
    }


def get_process_io(pid: int) -> dict | None:
    """
    Возвращает io_counters процесса: {"read_bytes", "write_bytes"}.
    write_bytes = сколько байт FFmpeg записал в сокет/файл.
    """
    try:
        p = psutil.Process(pid)
        io = p.io_counters()
        return {"read_bytes": io.read_bytes, "write_bytes": io.write_bytes}
    except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
        return None


def read_progress_from_log(log_path: Path) -> dict | None:
    """Читает последний блок -progress из лог-файла FFmpeg."""
    if not log_path.exists():
        return None
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8000))
            data = f.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    lines = data.splitlines()
    last_progress_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("progress="):
            last_progress_idx = i
    if last_progress_idx == -1:
        return None

    result = {}
    for i in range(last_progress_idx - 1, -1, -1):
        line = lines[i]
        if line.startswith("progress="):
            break
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result or None