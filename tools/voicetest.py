"""Проверка разделения собеседников на настоящих разных голосах (только macOS).

Диаризацию нельзя честно проверить одним голосом с разной скоростью — модель
сравнивает тембр, а не темп. Поэтому берём несколько системных голосов macOS,
склеиваем из них «системную дорожку» созвона и смотрим, разложит ли приложение
реплики по говорящим.

Запуск: python tools/voicetest.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import diarize, media, record  # noqa: E402
from app.settings import Settings  # noqa: E402

# Разные голоса — разный тембр. Русского голоса в macOS ровно один (Milena),
# поэтому для проверки самого разделения берём английские: языку модель
# безразлична, она слушает голос.
SCRIPT = [
    ("Samantha", "Hi everyone, thanks for joining the call today."),
    ("Daniel", "Good morning. I have the budget numbers ready for review."),
    ("Samantha", "Great. Let us start with the release scope and the deadlines."),
    ("Karen", "Sorry I am late. Can you repeat the part about the deadlines?"),
    ("Daniel", "We agreed to ship the onboarding screens by the twenty eighth."),
    ("Karen", "That works for me, but I need the final texts by Wednesday."),
    ("Samantha", "Understood. I will send the texts tomorrow evening."),
    ("Daniel", "One more thing, the contractor estimate is seven hundred forty thousand."),
    ("Karen", "Let us approve it and move on to the integration question."),
]

PAUSE = 0.35


def synth(voice: str, text: str, out: Path) -> np.ndarray:
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-r", "175", "-o", str(aiff), text],
                   check=True, capture_output=True)
    subprocess.run([str(media.tool("ffmpeg") or "ffmpeg"), "-y", "-i", str(aiff),
                    "-ac", "1", "-ar", str(media.SAMPLE_RATE), str(out)],
                   check=True, capture_output=True)
    aiff.unlink(missing_ok=True)
    return media.read_wav(out)


def build(work: Path) -> tuple[Path, list[record.Line], list[str]]:
    """Собирает дорожку и заодно — как оно должно получиться на самом деле."""
    chunks: list[np.ndarray] = []
    lines: list[record.Line] = []
    truth: list[str] = []
    position = 0.0
    silence = np.zeros(int(media.SAMPLE_RATE * PAUSE), dtype=np.float32)

    for i, (voice, text) in enumerate(SCRIPT):
        audio = synth(voice, text, work / f"part{i}.wav")
        length = audio.size / media.SAMPLE_RATE
        lines.append(record.Line(position, position + length, "them", text))
        truth.append(voice)
        chunks += [audio, silence]
        position += length + PAUSE

    track = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    path = work / "sys.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(media.SAMPLE_RATE)
        w.writeframes((np.clip(track, -1, 1) * 32767).astype(np.int16).tobytes())
    return path, lines, truth


def main() -> int:
    if sys.platform != "darwin":
        print("Нужен macOS: голоса берутся из системной команды say")
        return 2

    settings = Settings.load()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        print("Синтезирую дорожку из голосов:", ", ".join(sorted({v for v, _ in SCRIPT})))
        path, lines, truth = build(work)
        seconds = media.read_wav(path).size / media.SAMPLE_RATE
        print(f"Дорожка готова: {seconds:.1f} с, реплик {len(lines)}")

        print("Разбираю голоса…")
        spans = diarize.diarize(path, {**settings, "num_speakers": 0})
        found = len({s.speaker for s in spans})
        print(f"Найдено голосов: {found} (ожидалось {len(set(truth))})")

        keys, names = record.assign_others(lines, spans)

    # Проверяем не номера, а то, что важно: одному голосу — одна подпись.
    pairs = {}
    mistakes = 0
    for i, voice in enumerate(truth):
        label = names.get(keys[i], "?")
        pairs.setdefault(voice, label)
        mark = "✓" if pairs[voice] == label else "✗"
        if pairs[voice] != label:
            mistakes += 1
        print(f"  {mark} {voice:9} → {label:14} | {lines[i].text[:52]}")

    same_label = len(set(pairs.values())) < len(pairs)
    print()
    if found != len(set(truth)):
        print(f"✗ голосов найдено {found}, а в дорожке {len(set(truth))}")
    if same_label:
        print("✗ разные голоса получили одну подпись")
    if mistakes:
        print(f"✗ реплик с плавающей подписью: {mistakes}")
    if found == len(set(truth)) and not same_label and not mistakes:
        print("✓ каждый голос получил свою подпись")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
