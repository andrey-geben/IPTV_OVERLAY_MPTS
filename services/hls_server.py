"""Отдельный HTTP-сервер для HLS-прокси, независимый от Flask.

Зачем: MPTS читает HLS по HTTP. Если раздавать его через Flask, любой
перезапуск панели (деплой, падение, рестарт) рвёт эфир — в логах это
видно как «Connection to tcp://127.0.0.1:5000 failed: -138».

Этот сервер:
  * живёт в отдельном потоке, его не касаются рестарты Flask;
  * только читает файлы из data/hls (ничего не пишет);
  * отдаёт те же URL, что и Flask: /proxy/hls/<ch_id>/index.m3u8.
"""

from __future__ import annotations

import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_server: ThreadingHTTPServer | None = None
_port: int | None = None


class _HlsHandler(SimpleHTTPRequestHandler):
    """Раздаёт data/hls, понимая и /proxy_<id>/..., и /proxy/hls/<id>/..."""

    def __init__(self, *args, directory: str = ".", **kwargs):
        self._root = Path(directory)
        super().__init__(*args, directory=directory, **kwargs)

    def translate_path(self, path: str) -> str:
        rel = urllib.parse.urlsplit(path).path
        parts = [p for p in rel.split("/") if p and p not in (".", "..")]
        if len(parts) >= 3 and parts[0] == "proxy" and parts[1] == "hls":
            parts = [f"proxy_{parts[2]}"] + parts[3:]
        target = self._root.joinpath(*parts) if parts else self._root
        return str(target)

    def end_headers(self):
        # Плейлист меняется каждые 2-4 секунды — кэш только мешает.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):  # не засоряем консоль
        pass


def start_hls_server(hls_dir: Path | str, port: int = 8099, host: str = "127.0.0.1") -> int:
    """Запускает сервер один раз. Повторные вызовы возвращают тот же порт."""
    global _server, _port

    if _server is not None:
        return _port or port

    root = Path(hls_dir)
    root.mkdir(parents=True, exist_ok=True)

    handler = lambda *a, **kw: _HlsHandler(*a, directory=str(root), **kw)  # noqa: E731
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True

    threading.Thread(target=srv.serve_forever, name="hls-http", daemon=True).start()
    _server, _port = srv, port
    print(f"[hls] HTTP server: http://{host}:{port}/  (root: {root})")
    return port


def stop_hls_server() -> None:
    global _server, _port
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server, _port = None, None
