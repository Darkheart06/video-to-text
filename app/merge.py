"""Совмещение транскрипта и диаризации: кто именно произнёс каждую фразу."""

from __future__ import annotations

from dataclasses import dataclass, field

from .asr import Segment, Transcript
from .diarize import SpeakerSpan


@dataclass
class Turn:
    """Реплика одного спикера — то, что попадёт в транскрипт отдельной строкой."""
    start: float
    end: float
    speaker: int | None
    text: str
    segments: list[Segment] = field(default_factory=list)
    raw: str = ""            # как было сказано, до чистки от слов-паразитов

    @property
    def speaker_key(self) -> str:
        return "unknown" if self.speaker is None else f"S{self.speaker + 1}"


def _speaker_at(spans: list[SpeakerSpan], start: float, end: float) -> int | None:
    """Спикер с наибольшим перекрытием по времени с интервалом [start, end]."""
    best, best_overlap = None, 0.0
    for sp in spans:
        overlap = min(end, sp.end) - max(start, sp.start)
        if overlap > best_overlap:
            best, best_overlap = sp.speaker, overlap
    if best is not None and best_overlap > 0:
        return best
    # Фраза целиком попала в паузу — берём ближайшего по времени спикера.
    nearest, nearest_gap = None, float("inf")
    mid = (start + end) / 2
    for sp in spans:
        gap = 0.0 if sp.start <= mid <= sp.end else min(abs(sp.start - mid), abs(sp.end - mid))
        if gap < nearest_gap:
            nearest, nearest_gap = sp.speaker, gap
    return nearest if nearest_gap <= 2.0 else None


def assign_speakers(transcript: Transcript, spans: list[SpeakerSpan]) -> None:
    """Проставляет спикера каждому сегменту. Если внутри сегмента говорят разные
    люди — сегмент разрезается по словам."""
    if not spans:
        return

    new_segments: list[Segment] = []
    for seg in transcript.segments:
        if not seg.words:
            seg.speaker = _fmt(_speaker_at(spans, seg.start, seg.end))
            new_segments.append(seg)
            continue

        pieces: list[tuple[int | None, list]] = []
        for w in seg.words:
            spk = _speaker_at(spans, w.start, w.end)
            if pieces and pieces[-1][0] == spk:
                pieces[-1][1].append(w)
            else:
                pieces.append((spk, [w]))

        # Одиночные слова, «выпавшие» к соседу, приклеиваем обратно — это почти
        # всегда ошибка границы, а не реальная смена говорящего.
        pieces = _smooth(pieces)

        if len(pieces) == 1:
            seg.speaker = _fmt(pieces[0][0])
            new_segments.append(seg)
            continue

        for spk, words in pieces:
            text = "".join(w.text for w in words).strip()
            if not text:
                continue
            new_segments.append(Segment(
                start=words[0].start, end=words[-1].end,
                text=text, words=words, speaker=_fmt(spk),
            ))

    transcript.segments = new_segments


def _smooth(pieces: list[tuple[int | None, list]], min_words: int = 3) -> list:
    """Схлопывает короткие вставки чужого спикера внутри длинной реплики."""
    if len(pieces) <= 1:
        return pieces
    out = [list(p) for p in pieces]
    i = 1
    while i < len(out) - 1:
        if len(out[i][1]) < min_words and out[i - 1][0] == out[i + 1][0]:
            out[i - 1][1].extend(out[i][1])
            out[i - 1][1].extend(out[i + 1][1])
            del out[i:i + 2]
            continue
        i += 1
    # Слипшиеся соседи с одинаковым спикером
    merged = [out[0]]
    for piece in out[1:]:
        if piece[0] == merged[-1][0]:
            merged[-1][1].extend(piece[1])
        else:
            merged.append(piece)
    return [(p[0], p[1]) for p in merged]


def _fmt(spk: int | None) -> str | None:
    return None if spk is None else f"S{spk + 1}"


def build_turns(transcript: Transcript, max_gap: float = 1.5,
                max_chars: int = 1200) -> list[Turn]:
    """Склеивает подряд идущие сегменты одного спикера в реплики."""
    turns: list[Turn] = []
    for seg in transcript.segments:
        spk = None if seg.speaker is None else int(seg.speaker[1:]) - 1
        if (turns and turns[-1].speaker == spk
                and seg.start - turns[-1].end <= max_gap
                and len(turns[-1].text) < max_chars):
            t = turns[-1]
            t.end = seg.end
            t.text = f"{t.text} {seg.text}".strip()
            t.segments.append(seg)
        else:
            turns.append(Turn(seg.start, seg.end, spk, seg.text, [seg]))
    return _drop_slivers(turns)


def _drop_slivers(turns: list[Turn], min_words: int = 3,
                  min_seconds: float = 1.0) -> list[Turn]:
    """Убирает микрореплики, зажатые между двумя репликами одного человека.

    Обычно это не настоящая перебивка, а сползшая граница диаризации, поэтому
    текст возвращается тому, кто говорил вокруг, а не теряется.
    """
    if len(turns) < 3:
        return turns
    out = [turns[0]]
    i = 1
    while i < len(turns) - 1:
        cur, prev, nxt = turns[i], out[-1], turns[i + 1]
        sliver = (len(cur.text.split()) < min_words
                  and cur.end - cur.start < min_seconds
                  and prev.speaker == nxt.speaker
                  and cur.speaker != prev.speaker)
        if sliver:
            prev.end = nxt.end
            prev.text = f"{prev.text} {cur.text} {nxt.text}".strip()
            prev.segments.extend(cur.segments + nxt.segments)
            i += 2
            continue
        out.append(cur)
        i += 1
    if i == len(turns) - 1:
        out.append(turns[-1])
    return out
