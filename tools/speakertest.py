"""Проверка того, сколько голосов приложение находит в записи.

Разделение по голосам легко «улучшить» на глаз и незаметно испортить, поэтому
здесь всё считается: сколько голосов нашлось, насколько они похожи друг на
друга и — если сказать правильный ответ — насколько промахнулись.

Запуск:
    python tools/speakertest.py запись.m4a
    python tools/speakertest.py "~/Documents/Расшифровка записей/Созвон.wav" --было 3
    python tools/speakertest.py папка_с_записями --список правда.txt

Ключи работают и по-английски: `--truth 3`, `--list truth.txt`.
В файле правды по строке на запись: `имя файла<TAB>сколько человек`.
Годится любой файл, из которого ffmpeg достаёт звук, — не только .wav.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import diarize, media  # noqa: E402
from app.settings import Settings  # noqa: E402

# Что проверяем. Пустой словарь — настройки как есть.
VARIANTS: dict[str, dict] = {
    "без сведения": {"speaker_merge_similarity": 1.0},
    "числом 0.78": {"speaker_merge_auto": False},
    "числом 0.85": {"speaker_merge_auto": False, "speaker_merge_similarity": 0.85},
    "числом 0.72": {"speaker_merge_auto": False, "speaker_merge_similarity": 0.72},
    "по записи": {"speaker_merge_auto": True},
    "по записи +3%": {"speaker_merge_auto": True, "min_speaker_share": 0.03},
}


def voices(spans: list) -> dict[int, list]:
    out: dict[int, list] = {}
    for span in spans:
        out.setdefault(span.speaker, []).append(span)
    return out


def report(audio: np.ndarray, spans: list, threads: int) -> tuple[int, float, list[float]]:
    """Сколько голосов, самая похожая пара и минуты речи по голосам."""
    groups = voices(spans)
    minutes = sorted((sum(s.end - s.start for s in v) / 60 for v in groups.values()),
                     reverse=True)
    if len(groups) < 2:
        return len(groups), 0.0, minutes
    extractor = diarize.embedder(threads)
    prints = {k: diarize.voice_print(extractor, audio, v) for k, v in groups.items()}
    prints = {k: v for k, v in prints.items() if v is not None}
    keys = sorted(prints)
    worst = max((float(prints[a] @ prints[b])
                 for i, a in enumerate(keys) for b in keys[i + 1:]), default=0.0)
    return len(groups), worst, minutes


def main() -> int:
    plain = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not plain:
        print(__doc__)
        return 2

    def flag(*names: str) -> str:
        """Ключи есть и по-русски, и по-английски — инструмент двуязычный."""
        for name in names:
            if name in sys.argv:
                return sys.argv[sys.argv.index(name) + 1]
        return ""

    truth: dict[str, int] = {}
    listed = flag("--список", "--list")
    if listed:
        path = Path(listed).expanduser()
        for line in path.read_text("utf-8").split("\n"):
            name, _, count = line.partition("\t")
            if count.strip().isdigit():
                truth[name.strip()] = int(count.strip())
    said = flag("--было", "--truth")
    expected = int(said) if said.isdigit() else None

    target = Path(plain[0]).expanduser()
    known = media.AUDIO_EXT | {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
    files = ([f for f in sorted(target.iterdir()) if f.suffix.lower() in known]
             if target.is_dir() else [target])
    files = [f for f in files if f.exists()]
    if not files:
        print("Не нашёл ни одной записи")
        return 2

    settings = Settings.load()
    threads = int(settings["num_threads"])
    table: dict[str, list[tuple[str, int, int | None]]] = {}
    temp = tempfile.TemporaryDirectory()

    for source in files:
        want = truth.get(source.name, truth.get(source.stem, expected))
        # Диаризация ждёт моно 16 кГц — всё прочее пропускаем через ffmpeg.
        wav = source
        if source.suffix.lower() != ".wav":
            wav = media.extract_wav(source, Path(temp.name) / f"{source.stem}.wav")
        audio = media.read_wav(wav)
        print(f"\n{source.name} — {audio.size / media.SAMPLE_RATE / 60:.0f} мин"
              f"{f', на самом деле голосов: {want}' if want else ''}", flush=True)
        for name, options in VARIANTS.items():
            started = time.time()
            limits: list[str] = []

            def note(_frac: float, message: str, into: list[str] = limits) -> None:
                if "порог" in message:
                    into.append(message.split("порог", 1)[1].strip())

            try:
                spans = diarize.diarize(wav, {**settings, "num_speakers": 0, **options},
                                        progress=note)
            except Exception as exc:
                print(f"  {name:16} ошибка: {exc}")
                continue
            found, worst, minutes = report(audio, spans, threads)
            mark = ""
            if want:
                mark = " ✓" if found == want else f" мимо на {found - want:+d}"
            shown = " · ".join(f"{m:.1f}" for m in minutes[:8])
            print(f"  {name:16} голосов {found}{mark}, похожесть пары {worst:+.2f}, "
                  f"{time.time() - started:.0f} с"
                  f"{', порог ' + limits[-1] if limits else ''}   [{shown}]", flush=True)
            table.setdefault(name, []).append((source.name, found, want))

    temp.cleanup()
    scored = [(n, rows) for n, rows in table.items()
              if any(want for _, _, want in rows)]
    if scored:
        print("\nИтого по вариантам (только там, где известен правильный ответ):")
        for name, rows in scored:
            checked = [(found, want) for _, found, want in rows if want]
            exact = sum(1 for found, want in checked if found == want)
            off = sum(abs(found - want) for found, want in checked)
            print(f"  {name:16} точно {exact} из {len(checked)}, "
                  f"суммарная ошибка {off}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
