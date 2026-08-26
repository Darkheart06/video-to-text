"""Распознавание речи. Два движка: mlx-whisper (Apple Silicon) и faster-whisper."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import media
from .settings import Settings, resolve_asr_backend

Progress = Callable[[float, str], None]  # (доля 0..1, подпись)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None


@dataclass
class Transcript:
    segments: list[Segment]
    language: str
    duration: float
    backend: str
    model: str

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()


def _noop(_p: float, _m: str) -> None:
    pass


def transcribe(wav_path, settings: Settings, progress: Progress | None = None) -> Transcript:
    progress = progress or _noop
    backend = resolve_asr_backend(settings["asr_backend"])
    model_id = settings.whisper_repo(backend)
    language = None if settings["language"] == "auto" else settings["language"]

    audio = media.read_wav(wav_path)
    duration = len(audio) / media.SAMPLE_RATE

    if backend == "mlx":
        segments, detected = _transcribe_mlx(audio, model_id, language, settings, progress)
    else:
        segments, detected = _transcribe_faster(audio, model_id, language, settings,
                                                duration, progress)

    segments = _cleanup(segments)
    progress(1.0, "Распознавание завершено")
    return Transcript(
        segments=segments,
        language=detected or (language or "?"),
        duration=duration,
        backend=backend,
        model=model_id,
    )


# --- mlx-whisper -------------------------------------------------------------

def _transcribe_mlx(audio: np.ndarray, model_id: str, language: str | None,
                    settings: Settings, progress: Progress):
    import mlx_whisper

    chunks = media.split_points(audio, target_seconds=int(settings["chunk_seconds"]))
    total = len(audio) or 1
    out: list[Segment] = []
    detected = language

    for idx, (a, b) in enumerate(chunks):
        offset = a / media.SAMPLE_RATE
        progress(a / total, f"Распознавание, часть {idx + 1} из {len(chunks)}")
        res = mlx_whisper.transcribe(
            audio[a:b].astype(np.float32),
            path_or_hf_repo=model_id,
            language=language,
            word_timestamps=True,
            condition_on_previous_text=False,
            verbose=None,
        )
        detected = detected or res.get("language")
        # После первого куска фиксируем язык, чтобы он не «плавал» по файлу.
        language = language or res.get("language")
        for s in res.get("segments", []):
            words = [
                Word(float(w["start"]) + offset, float(w["end"]) + offset,
                     str(w.get("word", "")))
                for w in (s.get("words") or [])
                if w.get("start") is not None and w.get("end") is not None
            ]
            out.append(Segment(
                start=float(s["start"]) + offset,
                end=float(s["end"]) + offset,
                text=str(s.get("text", "")).strip(),
                words=words,
            ))
    return out, detected


# --- faster-whisper ----------------------------------------------------------

def _transcribe_faster(audio: np.ndarray, model_id: str, language: str | None,
                       settings: Settings, duration: float, progress: Progress):
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_id,
        device="cpu",
        compute_type=settings["compute_type"],
        cpu_threads=int(settings["num_threads"]),
    )
    seg_iter, info = model.transcribe(
        audio,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    out: list[Segment] = []
    total = max(duration, info.duration or duration, 1.0)
    for s in seg_iter:
        words = [
            Word(float(w.start), float(w.end), str(w.word))
            for w in (s.words or [])
            if w.start is not None and w.end is not None
        ]
        out.append(Segment(float(s.start), float(s.end), (s.text or "").strip(), words))
        progress(min(0.99, float(s.end) / total), "Распознавание речи")
    return out, info.language


# --- постобработка -----------------------------------------------------------

def _cleanup(segments: Iterable[Segment]) -> list[Segment]:
    """Убирает пустые сегменты и залипшие повторы одной и той же фразы."""
    out: list[Segment] = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        if out and text == out[-1].text and s.start - out[-1].end < 0.6:
            out[-1].end = s.end
            out[-1].words.extend(s.words)
            continue
        out.append(s)
    return out
