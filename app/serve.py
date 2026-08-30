"""Крошечный локальный сервер для звука и видео записи.

Окно приложения открыто из файла (`file://`), и WebKit не даёт странице
проигрывать другие файлы с диска: `<video src="file:///…">` молча остаётся
пустым. Поэтому медиа отдаём по http на 127.0.0.1 — соединение не выходит за
пределы машины, порт случайный, и наружу ничего не слушается.

Главное здесь — заголовок Range: без него плеер не умеет перематывать, а
именно перемотка и нужна ради меток.
"""

from __future__ import annotations

import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Что вообще можно отдавать. Список закрытый: сервер отдаёт файлы с диска, и
# «любое расширение» здесь означало бы «любой файл».
KINDS = {".wav", ".mp4", ".m4a", ".mp3", ".mov", ".vtt"}

_server: ThreadingHTTPServer | None = None
_roots: list[Path] = []


def start(roots: list[Path]) -> int:
    """Поднимает сервер (один на всё приложение) и возвращает порт."""
    global _server, _roots
    _roots = [Path(r).resolve() for r in roots if r]
    if _server is not None:
        return int(_server.server_address[1])
    _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return int(_server.server_address[1])


def allow(roots: list[Path]) -> None:
    """Папки с записями могли смениться в настройках."""
    global _roots
    _roots = [Path(r).resolve() for r in roots if r]


def _allowed(path: Path) -> bool:
    if path.suffix.lower() not in KINDS or not path.is_file():
        return False
    resolved = path.resolve()
    return any(resolved.is_relative_to(root) for root in _roots)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:      # тишина в консоли приложения
        pass

    def handle_one_request(self) -> None:
        # Плеер обрывает соединение на каждой перемотке — это норма, а не
        # ошибка, и в консоли приложения ей делать нечего.
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def do_GET(self) -> None:                   # noqa: N802 — имя из BaseHTTPRequestHandler
        query = parse_qs(urlparse(self.path).query)
        raw = (query.get("p") or [""])[0]
        path = Path(raw)
        if not raw or not _allowed(path):
            self.send_error(404)
            return

        size = path.stat().st_size
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        partial = False
        header = self.headers.get("Range", "")
        if header.startswith("bytes="):
            # «bytes=1000-» и «bytes=1000-2000» — этого хватает всем плеерам.
            first, _, last = header[len("bytes="):].partition("-")
            try:
                start = int(first) if first else 0
                end = int(last) if last else size - 1
            except ValueError:
                start, end = 0, size - 1
            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
            partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with path.open("rb") as source:
            source.seek(start)
            left = length
            while left > 0:
                chunk = source.read(min(262144, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return          # плеер перемотал — соединение закрылось
                left -= len(chunk)
