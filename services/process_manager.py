"""
Менеджер дочерних процессов (FFmpeg и др.).

Задачи:
  - запустить процесс и запомнить его в реестре;
  - корректно остановить (q → terminate → kill);
  - отдать статус (жив, PID, uptime, CPU, RAM);
  - сохранить логи процесса в файл;
  - ВОССТАНОВИТЬ реестр после перезапуска Flask (persistent registry).

Реестр сохраняется в logs_dir.parent / "processes.json" (по умолчанию — data/processes.json).
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


# ============================================================
# Восстановленный процесс (после перезапуска Flask)
# ============================================================

class _RestoredProcess:
    """
    Обёртка для восстановленного процесса. Имитирует subprocess.Popen.
    Ограничение: нет stdin (терминация только через terminate/kill).
    """
    def __init__(self, pid: int) -> None:
        self.pid = pid
        try:
            self._ps = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self._ps = None

    def poll(self) -> int | None:
        try:
            if self._ps and self._ps.is_running():
                return None
            return -1
        except psutil.NoSuchProcess:
            return -1

    @property
    def returncode(self) -> int | None:
        return self.poll()

    @property
    def stdin(self):
        return None  # восстановленный процесс не имеет stdin

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.time() + (timeout or 60)
        while time.time() < deadline:
            rc = self.poll()
            if rc is not None:
                return rc
            time.sleep(0.2)
        raise subprocess.TimeoutExpired("restored", timeout or 60)

    def terminate(self) -> None:
        try:
            if self._ps:
                self._ps.terminate()
        except psutil.NoSuchProcess:
            pass

    def kill(self) -> None:
        try:
            if self._ps:
                self._ps.kill()
        except psutil.NoSuchProcess:
            pass


def _cmdline_matches(pid: int, expected_cmd: list[str]) -> bool:
    """
    Проверяет, что процесс с данным PID — это ожидаемый exe (защита от PID-reuse).
    Сравниваем только имя exe: остальные аргументы могут содержать временные пути.
    """
    try:
        p = psutil.Process(pid)
        actual = p.cmdline()
        if not actual:
            return False
        expected_exe = Path(expected_cmd[0]).name.lower()
        actual_exe = Path(actual[0]).name.lower()
        return expected_exe == actual_exe
    except Exception:
        return False


# ============================================================
# ProcessManager
# ============================================================

class ProcessManager:
    def __init__(self, logs_dir: Path, registry_path: Path | None = None) -> None:
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._processes: dict[str, dict] = {}

        # По умолчанию — рядом с logs_dir (data/processes.json)
        self.registry_path = (
            Path(registry_path) if registry_path
            else self.logs_dir.parent / "processes.json"
        )

        self._restore_from_disk()

    # ---------- Persistent registry ----------

    def _save_registry(self) -> None:
        """Атомарно сохраняет реестр на диск (только живые процессы)."""
        with self._lock:
            payload = []
            for name, info in self._processes.items():
                proc = info["proc"]
                if proc.poll() is not None:
                    continue
                payload.append({
                    "name": name,
                    "pid": proc.pid,
                    "command": info["command"],
                    "started_at": info["started_at"],
                    "log_path": str(info["log_path"]),
                })

        try:
            tmp = self.registry_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.registry_path)
        except Exception as exc:
            print(f"[pm] Failed to save registry: {exc}")

    def _restore_from_disk(self) -> None:
        """Восстанавливает реестр после перезапуска Flask."""
        if not self.registry_path.exists():
            return

        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[pm] Failed to read registry: {exc}")
            return

        restored = 0
        for entry in payload:
            name = entry.get("name")
            pid = entry.get("pid")
            command = entry.get("command", [])
            started_at = entry.get("started_at", time.time())
            log_path = Path(entry.get("log_path", self.logs_dir / f"{name}.log"))

            if not name or not pid or not command:
                continue
            if not _cmdline_matches(pid, command):
                continue

            # Лог открываем в append (не перезаписываем)
            try:
                log_file = open(log_path, "ab")
            except Exception:
                continue

            try:
                ps = psutil.Process(pid)
                ps.cpu_percent(interval=None)
            except psutil.Error:
                ps = None

            self._processes[name] = {
                "proc": _RestoredProcess(pid),
                "ps": ps,
                "log_file": log_file,
                "log_path": log_path,
                "started_at": started_at,
                "command": command,
            }
            restored += 1

        if restored:
            print(f"[pm] Restored {restored} processes from registry")

        # Обновляем файл — чистим мёртвые записи
        self._save_registry()

    # ---------- Public API ----------

    def start(self, name: str, command: list[str]) -> dict:
        with self._lock:
            existing = self._processes.get(name)
            if existing and existing["proc"].poll() is None:
                return {"ok": False, "error": f"Процесс '{name}' уже запущен (pid {existing['proc'].pid})"}
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

            try:
                ps = psutil.Process(proc.pid)
                ps.cpu_percent(interval=None)
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

        self._save_registry()
        return {"ok": True, "pid": proc.pid, "name": name, "log_path": str(log_path)}

    def stop(self, name: str) -> dict:
        with self._lock:
            info = self._processes.get(name)
            if not info:
                return {"ok": False, "error": f"Процесс '{name}' не найден в реестре"}

            proc = info["proc"]

            if proc.poll() is not None:
                self._cleanup_locked(name)
                result = {"ok": True, "already_stopped": True, "exit_code": proc.returncode}
            else:
                try:
                    if proc.stdin:
                        try:
                            proc.stdin.write(b"q\n")
                            proc.stdin.flush()
                        except (BrokenPipeError, OSError):
                            pass
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
                result = {"ok": True, "exit_code": exit_code}

        self._save_registry()
        return result

    def status(self, name: str) -> dict:
        with self._lock:
            info = self._processes.get(name)
            if not info:
                return {"exists": False, "running": False}

            proc = info["proc"]
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

            ps = info.get("ps")
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
        with self._lock:
            return [
                {
                    "name": name,
                    "running": info["proc"].poll() is None,
                    "pid": info["proc"].pid,
                }
                for name, info in self._processes.items()
            ]

    def get_command(self, name: str) -> list[str] | None:
        """Возвращает сохранённую команду процесса или None."""
        with self._lock:
            info = self._processes.get(name)
            return info["command"] if info else None

    def stop_all(self) -> list[str]:
        with self._lock:
            names = list(self._processes.keys())

        stopped = []
        for name in names:
            try:
                res = self.stop(name)
                if res.get("ok"):
                    stopped.append(name)
            except Exception as exc:
                print(f"[pm] Ошибка остановки {name}: {exc}")
        return stopped

    # ---------- Internal ----------

    def _cleanup_locked(self, name: str) -> None:
        """
        Удаляет процесс из реестра и закрывает лог.
        Вызывается ТОЛЬКО при уже взятом self._lock.
        """
        info = self._processes.pop(name, None)
        if info:
            try:
                info["log_file"].close()
            except Exception:
                pass