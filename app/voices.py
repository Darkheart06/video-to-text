"""Знакомые голоса: приложение запоминает, как звучит человек.

Разделение по голосам внутри одной записи работает без всякой памяти: кто с
кем совпал, видно из самой записи. А вот «это Леонид» из записи не следует
никак — имя приходится ставить руками каждый раз заново.

Здесь лежит память между записями: голосовой отпечаток человека, снятый с уже
разобранной записи, где имя уже проставлено. В следующий раз голос узнаётся
сам.

**Обучение запускается отдельно, по команде.** Автоматически запоминать
опасно: одна ошибка разделения — и приложение навсегда выучит, что Леонид
звучит как его собеседница. Поэтому человек сам говорит: «вот эту запись
запомни», уже посмотрев, что имена расставлены верно.

Никакого дообучения нейросети здесь нет и не нужно: мы не меняем модель, а
храним рядом с ней несколько векторов на человека — так же, как это делает
разбор внутри записи. Дообучение модели потребовало бы часов речи каждого
человека и видеокарты, а выигрыш дало бы там, где мы и так не ошибаемся.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np

from . import diarize, media
from .settings import ROOT

# Рядом с установленным приложением: переживает обновления, не уезжает в
# папку с результатами и не попадает в архив записей.
STORE = ROOT / "voices.json"

# Сколько отпечатков помним на человека. Один вектор — это один микрофон и
# одно настроение; несколько дают запас на «сегодня простужен» и «звонит из
# машины».
PER_PERSON = 12

# Куски короче этого на отпечаток не годятся: там больше комнаты, чем голоса.
MIN_PIECE = 1.5


def load() -> dict[str, list[np.ndarray]]:
    """Все запомненные голоса: имя -> список отпечатков."""
    if not STORE.exists():
        return {}
    try:
        data = json.loads(STORE.read_text("utf-8"))
    except Exception:
        return {}
    out: dict[str, list[np.ndarray]] = {}
    for name, vectors in (data.get("people") or {}).items():
        kept = [np.array(v, dtype=np.float32) for v in vectors if v]
        if kept:
            out[str(name)] = kept
    return out


def save(people: dict[str, list[np.ndarray]]) -> None:
    STORE.write_text(json.dumps(
        {"version": 1,
         "people": {name: [v.tolist() for v in vectors[-PER_PERSON:]]
                    for name, vectors in people.items() if vectors}},
        ensure_ascii=False), "utf-8")


def names() -> list[dict]:
    """Список для интерфейса: кого мы знаем и по скольким отпечаткам."""
    return [{"name": name, "prints": len(vectors)}
            for name, vectors in sorted(load().items())]


def forget(name: str) -> bool:
    people = load()
    if name not in people:
        return False
    people.pop(name)
    save(people)
    return True


def match(print_: np.ndarray, floor: float = 0.65,
          margin: float = 0.05, people: dict | None = None) -> tuple[str, float]:
    """Кого напоминает этот голос.

    Решает не только уровень похожести, но и отрыв от второго кандидата: если
    двое похожи одинаково, честнее не называть никого, чем подписать наугад.
    """
    people = load() if people is None else people
    if not people:
        return "", 0.0
    scores = sorted(((max(float(print_ @ v) for v in vectors), name)
                     for name, vectors in people.items()), reverse=True)
    best, name = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    if best < floor or best - second < margin:
        return "", best
    return name, best


def remember(name: str, prints: list[np.ndarray]) -> int:
    """Добавляет отпечатки человеку. Возвращает, сколько всего стало."""
    people = load()
    kept = people.setdefault(name, [])
    kept.extend(prints)
    del kept[:-PER_PERSON]
    save(people)
    return len(kept)


def identify(audio, spans, threads: int = 4,
             people: dict | None = None) -> dict[int, str]:
    """Узнаёт запомненных людей среди разобранных голосов записи.

    Возвращает номер голоса -> имя. Одно имя достаётся одному голосу: если
    двое похожи на Леонида, Леонид тут ровно один — тот, кто похож сильнее.
    """
    people = load() if people is None else people
    if not people or not spans:
        return {}
    grouped: dict[int, list] = {}
    for span in spans:
        grouped.setdefault(span.speaker, []).append(span)
    extractor = diarize.embedder(threads)
    guesses = []
    for speaker, pieces in grouped.items():
        vector = diarize.voice_print(extractor, audio, pieces, seconds=30.0)
        if vector is None:
            continue
        name, score = match(vector, people=people)
        if name:
            guesses.append((score, speaker, name))
    found: dict[int, str] = {}
    taken: set[str] = set()
    for _score, speaker, name in sorted(guesses, reverse=True):
        if name in taken:
            continue
        taken.add(name)
        found[speaker] = name
    return found


# --- обучение по готовой записи ---------------------------------------------

def learn(result_path: str | Path, threads: int = 4,
          skip: tuple[str, ...] = ()) -> dict:
    """Запоминает голоса из разобранной записи — по команде человека.

    Берём только тех, кому уже дано настоящее имя: «Спикер 2» и «Собеседник 1»
    ничего не значат и запоминанию не подлежат. Звук нужен тот же самый —
    рядом с записью лежит её `.wav`; если его нет, учить не на чем.
    """
    path = Path(result_path)
    data = json.loads(path.read_text("utf-8"))

    labels = {key: str(value.get("label") or key)
              for key, value in (data.get("speakers") or {}).items()}
    spans: dict[str, list] = {}
    for turn in data.get("turns") or []:
        key = str(turn.get("speaker") or "")
        name = labels.get(key, "")
        if not key or not name or _is_placeholder(name) or name in skip:
            continue
        start, end = float(turn.get("start") or 0), float(turn.get("end") or 0)
        if end - start >= MIN_PIECE:
            spans.setdefault(name, []).append(
                diarize.SpeakerSpan(start, end, 0))

    # Имена проверяем до того, как трогать звук: доставать его из исходника —
    # минута работы, а запоминать всё равно будет нечего.
    if not spans:
        return {"ok": False, "error": "no-names"}

    stem = path.name[: -len(".result.json")]
    audio_path = path.parent / f"{stem}.wav"
    temporary = None
    if not audio_path.exists():
        # У записанных созвонов звук лежит рядом, у разобранных файлов — нет.
        # Тогда берём исходник, из которого делали расшифровку.
        source = str((data.get("meta") or {}).get("source") or "")
        if not source or not Path(source).exists():
            return {"ok": False, "error": "no-audio"}
        temporary = Path(tempfile.mkdtemp()) / "learn.wav"
        try:
            media.extract_wav(source, temporary)
        except Exception:
            shutil.rmtree(temporary.parent, ignore_errors=True)
            return {"ok": False, "error": "no-audio"}
        audio_path = temporary

    audio = media.read_wav(audio_path)
    extractor = diarize.embedder(threads)
    learned: dict[str, int] = {}
    for name, pieces in spans.items():
        prints = []
        # Несколько отпечатков вместо одного: разные куски речи одного
        # человека звучат по-разному, и хранить лучше набор, чем среднее.
        pieces.sort(key=lambda s: -(s.end - s.start))
        for group in (pieces[i::3] for i in range(3)):
            if not group:
                continue
            vector = diarize.voice_print(extractor, audio, group, seconds=30.0)
            if vector is not None:
                prints.append(vector)
        if prints:
            learned[name] = remember(name, prints)
    if temporary is not None:
        shutil.rmtree(temporary.parent, ignore_errors=True)
    if not learned:
        return {"ok": False, "error": "no-speech"}
    return {"ok": True, "learned": learned}


def _is_placeholder(name: str) -> bool:
    """«Спикер 2», «Собеседник 1», «Speaker 3» — это не имена."""
    lowered = name.strip().lower()
    return (not lowered
            or lowered.split()[0] in ("спикер", "собеседник", "speaker",
                                      "person", "them", "я", "me", "unknown",
                                      "неизвестный"))
