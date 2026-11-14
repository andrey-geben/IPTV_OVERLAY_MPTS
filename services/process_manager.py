"""
Менеджер дочерних процессов (FFmpeg и др.).

Задачи:
  - запустить процесс и запомнить его в реестре;
  - корректно остановить (q → terminate → kill);
  - отдать статус (жив, PID, uptime, CPU, RAM);
  - сохранить логи процесса в файл.

Этот модуль НЕ знает ничего про Flask и про FFmpeg-специфику
(какие ключи у ffmpeg — не его дело). Ему на вход дают готовую команду.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


class ProcessManager:
    """
    Хранит реестр процессов в памяти: {имя: {proc, ps, log_file, ...}}.
    Все операции защищены lock, т.к. Flask может обрабатывать
    несколько запросов параллельно в разных потоках.
    """

    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._processes: dict[str, dict] = {}

    # ---------- Публичный API ----------

    def start(self, name: str, command: list[str]) -> dict:
        """
        Запустить процесс под именем `name`.
        Команда — список строк, например: ["ffmpeg", "-re", "-i", "..."].

        Возвращает dict: {"ok": True, "pid": ...} или {"ok": False, "error": ...}.
        """
        with self._lock:
            existing = self._processes.get(name)
            if existing and existing["proc"].poll() is None:
                return {"ok": False, "error": f"Процесс '{name}' уже запущен (pid {existing['proc'].pid})"}

            # Если предыдущий с таким именем уже умер — вычистим его.
            if existing:
                self._cleanup_locked(name)

            log_path = self.logs_dir / f"{name}.log"
            log_file = open(log_path, "wb")

            try:
                proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    # На Windows создаём процесс в отдельной группе,
                    # чтобы в будущем можно было посылать сигнал всей группе.
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32" else 0
                    ),
                )
            except FileNotFoundError as exc:
                log_file.close()
                return {"ok": False, "error": f"Команда не найдена: {exc}"}
            except Exception as exc:
                log_file.close()
                return {"ok": False, "error": str(exc)}

            # psutil.Process нужен для сбора CPU/RAM.
            # cpu_percent(interval=None) работает только при повторных вызовах,
            # поэтому создаём объект один раз и храним — а не новый на каждый запрос.
            try:
                ps = psutil.Process(proc.pid)
                ps.cpu_percent(interval=None)  # «прогрев» — первый вызов всегда 0.0
            except psutil.Error:
                ps = None

            self._processes[name] = {
                "proc": proc,
                "ps": ps,
                "log_file": log_file,
                "log_path": log_path,
                "started_at": time.time(),
                "command": command,
            }

            return {"ok": True, "pid": proc.pid, "name": name, "log_path": str(log_path)}

    def stop(self, name: str) -> dict:
        """
        Остановить процесс. Три уровня:
          1. Попросить FFmpeg выйти красиво через stdin 'q'.
          2. Если не выйдет за 3 сек — terminate().
          3. Если и это не сработает за 2 сек — kill().
        """
        with self._lock:
            info = self._processes.get(name)
            if not info:
                return {"ok": False, "error": f"Процесс '{name}' не найден в реестре"}

            proc: subprocess.Popen = info["proc"]

            if proc.poll() is not None:
                # Уже остановлен.
                self._cleanup_locked(name)
                return {"ok": True, "already_stopped": True, "exit_code": proc.returncode}

            try:
                if proc.stdin:
                    try:
                        proc.stdin.write(b"q\n")
                        proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        pass  # Процесс не читает stdin — не страшно.
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
            except Exception:
                pass

            exit_code = proc.returncode
            self._cleanup_locked(name)
            return {"ok": True, "exit_code": exit_code}

    def status(self, name: str) -> dict:
        """
        Статус процесса.
        Если процесса нет в реестре — {"exists": False, "running": False}.
        Если в реестре есть, но уже умер — {"exists": True, "running": False, "exit_code": ...}.
        Иначе — подробный словарь с PID, uptime, CPU, RAM.
        """
        with self._lock:
            info = self._processes.get(name)
            if not info:
                return {"exists": False, "running": False}

            proc: subprocess.Popen = info["proc"]
            pid = proc.pid

            if proc.poll() is not None:
                return {
                    "exists": True,
                    "running": False,
                    "pid": pid,
                    "exit_code": proc.returncode,
                }

            result = {
                "exists": True,
                "running": True,
                "name": name,
                "pid": pid,
                "uptime_sec": int(time.time() - info["started_at"]),
                "command": " ".join(info["command"]),
                "log_path": str(info["log_path"]),
            }

            ps: psutil.Process | None = info.get("ps")
            if ps is not None:
                try:
                    with ps.oneshot():
                        result["cpu_percent"] = ps.cpu_percent(interval=None)
                        result["memory_mb"] = round(
                            ps.memory_info().rss / (1024 * 1024), 1
                        )
                except psutil.Error:
                    pass

            return result

    def list_all(self) -> list[dict]:
        """Короткий список всех процессов в реестре."""
        with self._lock:
            return [
                {
                    "name": name,
                    "running": info["proc"].poll() is None,
                    "pid": info["proc"].pid,
                }
                for name, info in self._processes.items()
            ]

    # ---------- Внутренние хелперы ----------

    def _cleanup_locked(self, name: str) -> None:
        """
        Удалить процесс из реестра и закрыть лог-файл.
        Вызывается ТОЛЬКО при уже взятом self._lock,
        поэтому без `with self._lock` внутри.
        """
        info = self._processes.pop(name, None)
        if info:
            try:
                info["log_file"].close()
            except Exception:
                pass