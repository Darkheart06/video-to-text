"""Восстановление записи из рабочей папки.

Если приложение закрыли или оно упало посреди созвона, звук никуда не делся:
помощник захвата пишет `mic.pcm` и `sys.pcm` в рабочую папку и продолжает это
делать, даже оставшись без хозяина. Этот инструмент собирает из такой папки
нормальную запись: расшифровывает обе дорожки, убирает эхо, разбирает голоса,
делает саммари и кладёт файлы туда же, куда их положило бы само приложение.

Запуск:
    python tools/recover.py ~/Library/Application\\ Support/VideoToText/.work/rec-a21c
    python tools/recover.py <папка> --minutes 7 --title "Начало созвона"

`--minutes` берёт только начало записи — например, когда остальное уже
разобрано, а восстановить нужно потерянный кусок.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import cleanup, record  # noqa: E402
from app.settings import WORK_DIR, Settings  # noqa: E402


def read_track(path: Path, limit: int) -> np.ndarray:
    if not path.exists():
        return np.zeros(0, dtype=np.float32)
    data = path.read_bytes()
    if limit:
        data = data[:limit]
    return record._to_float(data)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=")[0]: a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 2

    work = Path(args[0]).expanduser()
    if not work.is_dir():
        print(f"Не нашёл папку: {work}")
        return 2

    minutes = 0.0
    if "--minutes" in flags:
        pos = sys.argv.index(flags["--minutes"])
        minutes = float(sys.argv[pos + 1] if "=" not in flags["--minutes"]
                        else flags["--minutes"].split("=")[1])
    title = ""
    if "--title" in flags:
        pos = sys.argv.index(flags["--title"])
        title = sys.argv[pos + 1] if "=" not in flags["--title"] \
            else flags["--title"].split("=")[1]

    limit = int(minutes * 60 * record.BYTES_PER_SECOND) if minutes else 0
    mic = read_track(work / "mic.pcm", limit)
    spk = read_track(work / "sys.pcm", limit)
    if mic.size == 0 and spk.size == 0:
        print("В папке нет звука")
        return 2

    room = spk.size == 0
    seconds = max(mic.size, spk.size) / record.SAMPLE_RATE
    print(f"Нашёл {seconds / 60:.1f} мин звука"
          f"{' (только микрофон — встреча)' if room else ' на двух дорожках'}")

    settings = Settings.load()
    steno = record.Stenographer(settings)
    stamp = time.strftime("%Y-%m-%d %H-%M", time.localtime(work.stat().st_mtime))

    # Работаем на копии: сборка в конце удаляет свою папку, а исходную портить
    # нельзя — она единственный экземпляр записи. Заодно так соблюдается
    # обрезка по времени: в копию попадает ровно то, что расшифровано.
    spare = WORK_DIR / f"recover-{uuid.uuid4().hex[:8]}"
    spare.mkdir(parents=True, exist_ok=True)
    for name, track in (("mic.pcm", mic), ("sys.pcm", spk)):
        if track.size:
            (spare / name).write_bytes(
                (np.clip(track, -1, 1) * 32767).astype(np.int16).tobytes())

    steno.session = record.Session(
        id=uuid.uuid4().hex[:10], started_at=time.time() - seconds,
        directory=spare, mode="room" if room else "call",
        title=title or (f"Встреча {stamp}" if room else f"Созвон {stamp}"),
        stamp=stamp, preset=settings.get("preset", "meeting"),
    )

    lines: list[record.Line] = []
    tracks = ((mic, "room"),) if room else ((mic, "me"), (spk, "them"))
    for track, who in tracks:
        if track.size < record.SAMPLE_RATE:
            continue
        print(f"Расшифровываю дорожку «{who}» — {track.size / record.SAMPLE_RATE / 60:.1f} мин…",
              flush=True)
        lines.extend(steno._transcribe(track, 0.0, who))
        print(f"  реплик: {len(lines)}", flush=True)

    if not lines:
        print("Речь не распознана")
        return 1

    if not room and settings.get("record_dedupe", True):
        before = len(lines)
        lines = record.drop_echo(lines, mic, spk, 0.0)
        print(f"Эхо между дорожками: убрано {before - len(lines)} повторов")
    cleanup.clean_turns(lines, bool(settings.get("transcript_cleanup", True)))
    lines.sort(key=lambda item: item.start)
    steno.session.lines = lines

    print("Собираю запись: голоса, саммари, файлы…", flush=True)
    steno._finish()

    session = steno.session
    print(f"\nГотово: {session.title}")
    for key, path in (session.files or {}).items():
        print(f"  {key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
