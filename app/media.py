"""Работа с медиафайлами: проверка, извлечение аудио, нарезка на куски."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff"}
SUPPORTED_EXT = VIDEO_EXT | AUDIO_EXT

# Установленное приложение носит ffmpeg с собой, в папке bin рядом с кодом.
BUNDLED_BIN = Path(__file__).resolve().parent.parent / "bin"


class MediaError(RuntimeError):
    pass


def tool(name: str) -> str | None:
    """Свой ffmpeg приоритетнее системного: так приложение не зависит от того,
    что установлено на конкретной машине."""
    bundled = BUNDLED_BIN / name
    if bundled.exists() and os.access(bundled, os.X_OK):
        return str(bundled)
    return shutil.which(name)


def require_ffmpeg() -> None:
    if not tool("ffmpeg"):
        raise MediaError(
            "ffmpeg не найден. Установите его: brew install ffmpeg"
        )


@dataclass
class MediaInfo:
    path: Path
    duration: float
    has_audio: bool
    has_video: bool


def probe(path: str | Path) -> MediaInfo:
    require_ffmpeg()
    path = Path(path)
    if not path.exists():
        raise MediaError(f"Файл не найден: {path}")
    if path.suffix.lower() not in SUPPORTED_EXT:
        raise MediaError(
            f"Формат {path.suffix or '?'} не поддерживается. "
            f"Поддерживаются: {', '.join(sorted(SUPPORTED_EXT))}"
        )

    ffprobe = tool("ffprobe")
    info = _probe_ffprobe(ffprobe, path) if ffprobe else _probe_ffmpeg(path)

    if not info.has_audio:
        raise MediaError("В файле нет звуковой дорожки — распознавать нечего.")
    return info


def _probe_ffprobe(ffprobe: str, path: Path) -> MediaInfo:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise MediaError(f"Не удалось прочитать файл: {out.stderr.strip()[:400]}")

    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])

    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        for s in streams:
            try:
                duration = max(duration, float(s.get("duration") or 0))
            except (TypeError, ValueError):
                pass

    return MediaInfo(
        path=path, duration=duration,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        has_video=any(s.get("codec_type") == "video" for s in streams),
    )


def _probe_ffmpeg(path: Path) -> MediaInfo:
    """Запасной разбор, когда рядом только ffmpeg без ffprobe: он всё равно
    печатает сведения о файле в поток ошибок."""
    out = subprocess.run([tool("ffmpeg"), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True)
    text = out.stderr
    if "Invalid data" in text or "No such file" in text:
        raise MediaError(f"Не удалось прочитать файл: {text.strip()[-300:]}")

    duration = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return MediaInfo(
        path=path, duration=duration,
        has_audio=bool(re.search(r"Stream #.*: Audio:", text)),
        has_video=bool(re.search(r"Stream #.*: Video:", text)),
    )


def extract_wav(src: str | Path, dst: str | Path) -> Path:
    """Извлекает моно-дорожку 16 кГц — единый вход для Whisper и диаризации."""
    require_ffmpeg()
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not dst.exists():
        raise MediaError(f"Не удалось извлечь звук: {res.stderr.strip()[:400]}")
    return dst


def read_wav(path: str | Path) -> np.ndarray:
    """Читает 16 кГц моно WAV в float32 [-1, 1] без внешних зависимостей."""
    import wave

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1:
            raise MediaError("Ожидался моно WAV 16 кГц")
        frames = w.readframes(w.getnframes())
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return data


def split_points(audio: np.ndarray, target_seconds: int = 600,
                 search_seconds: int = 45) -> list[tuple[int, int]]:
    """Делит аудио на куски примерно по target_seconds, разрезая в самом тихом месте.

    Возвращает список пар (начало, конец) в отсчётах. Разрез в тишине нужен,
    чтобы не рвать слово пополам между кусками.
    """
    n = len(audio)
    target = int(target_seconds * SAMPLE_RATE)
    search = int(search_seconds * SAMPLE_RATE)
    if n <= target + search:
        return [(0, n)]

    # Энергия по окнам 100 мс
    win = int(0.1 * SAMPLE_RATE)
    usable = (n // win) * win
    energy = np.abs(audio[:usable].reshape(-1, win)).mean(axis=1)

    chunks: list[tuple[int, int]] = []
    start = 0
    while start < n:
        ideal = start + target
        if ideal >= n - search:
            chunks.append((start, n))
            break
        lo = max(start + win, ideal - search) // win
        hi = min(usable, ideal + search) // win
        if hi <= lo:
            cut = ideal
        else:
            cut = (lo + int(np.argmin(energy[lo:hi]))) * win
        chunks.append((start, cut))
        start = cut
    return chunks
