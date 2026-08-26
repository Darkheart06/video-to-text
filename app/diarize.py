"""Разделение по спикерам (диаризация) через sherpa-onnx. Работает офлайн."""

from __future__ import annotations

import shutil
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import media
from .settings import MODELS_DIR, Settings

SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMB_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/wespeaker_en_voxceleb_CAM%2B%2B.onnx"
)

SEG_PATH = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_PATH = MODELS_DIR / "wespeaker_en_voxceleb_CAMPP.onnx"

Progress = Callable[[float, str], None]


class DiarizationError(RuntimeError):
    pass


@dataclass
class SpeakerSpan:
    start: float
    end: float
    speaker: int


def models_ready() -> bool:
    return SEG_PATH.exists() and EMB_PATH.exists()


def download_models(progress: Progress | None = None) -> None:
    """Скачивает модели диаризации (~35 МБ) в папку models/."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    if not SEG_PATH.exists():
        report(0.0, "Скачивание модели сегментации")
        tmp = MODELS_DIR / "segmentation.tar.bz2"
        _download(SEG_URL, tmp, lambda f: report(f * 0.3, "Скачивание модели сегментации"))
        with tarfile.open(tmp, "r:bz2") as tf:
            try:
                tf.extractall(MODELS_DIR, filter="data")  # Python 3.12+
            except TypeError:
                tf.extractall(MODELS_DIR)
        tmp.unlink(missing_ok=True)
        if not SEG_PATH.exists():
            raise DiarizationError("Архив с моделью сегментации распакован не так, как ожидалось")

    if not EMB_PATH.exists():
        report(0.3, "Скачивание модели голосовых отпечатков")
        _download(EMB_URL, EMB_PATH,
                  lambda f: report(0.3 + f * 0.7, "Скачивание модели голосовых отпечатков"))

    report(1.0, "Модели готовы")


def _download(url: str, dst: Path, on_progress: Callable[[float], None]) -> None:
    tmp = dst.with_suffix(dst.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                block = resp.read(1 << 16)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                if total:
                    on_progress(min(1.0, done / total))
        shutil.move(str(tmp), str(dst))
    finally:
        Path(tmp).unlink(missing_ok=True)


def diarize(wav_path, settings: Settings, progress: Progress | None = None) -> list[SpeakerSpan]:
    try:
        import sherpa_onnx
    except ImportError as exc:  # pragma: no cover
        raise DiarizationError(
            "Не установлен sherpa-onnx — разделение по спикерам недоступно."
        ) from exc

    if not models_ready():
        download_models(progress)

    threads = int(settings["num_threads"])
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(SEG_PATH)
            ),
            num_threads=threads,
            provider="cpu",
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(EMB_PATH), num_threads=threads, provider="cpu"
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=int(settings["num_speakers"]) or -1,
            threshold=float(settings["cluster_threshold"]),
        ),
        min_duration_on=float(settings["min_duration_on"]),
        min_duration_off=float(settings["min_duration_off"]),
    )
    engine = sherpa_onnx.OfflineSpeakerDiarization(config)

    audio = media.read_wav(wav_path)
    if engine.sample_rate != media.SAMPLE_RATE:  # pragma: no cover
        raise DiarizationError(
            f"Модель ждёт {engine.sample_rate} Гц, а аудио {media.SAMPLE_RATE} Гц"
        )

    if progress:
        progress(0.0, "Разделение по спикерам")

    def callback(processed: int, total: int) -> int:
        if progress and total:
            progress(min(0.99, processed / total), "Разделение по спикерам")
        return 0

    try:
        result = engine.process(audio, callback=callback)
    except TypeError:
        result = engine.process(audio)

    spans = [
        SpeakerSpan(float(s.start), float(s.end), int(s.speaker))
        for s in result.sort_by_start_time()
    ]
    spans = _renumber(spans)
    if settings["num_speakers"] and int(settings["num_speakers"]) > 0:
        # Человек сам сказал, сколько их — сводить кластеры не надо.
        if progress:
            progress(1.0, f"Найдено спикеров: {len({s.speaker for s in spans})}")
        return spans
    spans = refine(spans, audio, settings, progress)
    if progress:
        progress(1.0, f"Найдено спикеров: {len({s.speaker for s in spans})}")
    return spans


# --- сведение одного человека, разбитого на несколько голосов ---------------

def embedder(threads: int):
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(EMB_PATH), num_threads=threads, provider="cpu"
        )
    )


def voice_print(extractor, audio: np.ndarray, spans: list[SpeakerSpan],
                seconds: float = 45.0) -> np.ndarray | None:
    """Голосовой отпечаток по нескольким кускам речи одного кластера."""
    stream = extractor.create_stream()
    used = 0.0
    for span in sorted(spans, key=lambda s: -(s.end - s.start)):
        if used >= seconds:
            break
        a, b = int(span.start * media.SAMPLE_RATE), int(span.end * media.SAMPLE_RATE)
        piece = audio[a:b]
        if piece.size < media.SAMPLE_RATE * 0.6:
            continue
        stream.accept_waveform(sample_rate=media.SAMPLE_RATE, waveform=piece)
        used += piece.size / media.SAMPLE_RATE
    if used <= 0:
        return None
    stream.input_finished()
    vector = np.array(extractor.compute(stream), dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else None


def refine(spans: list[SpeakerSpan], audio: np.ndarray,
           settings, progress: Progress | None = None) -> list[SpeakerSpan]:
    """Сводит кластеры, которые на самом деле один и тот же человек.

    Кластеризация внутри sherpa-onnx охотно дробит одного говорящего: на
    настоящей получасовой встрече она нашла 28 «голосов», причём отдельные
    пары были похожи на 0.94–0.97, тогда как у по-настоящему разных людей
    похожесть 0.0–0.7. Поэтому после разделения сравниваем голосовые
    отпечатки и склеиваем те, что модель сама считает одним голосом.

    Склеиваем строго: две группы сходятся, только если **каждый** кластер
    одной похож на **каждый** кластер другой. Иначе получается цепочка —
    A похож на B, B на C — и в один «голос» съезжается половина встречи,
    хотя A и C совсем разные люди.

    Заодно обрывки в пару секунд отдаём ближайшему голосу: отдельный
    «Спикер 6» ради одного «угу» только мешает читать.
    """
    groups: dict[int, list[SpeakerSpan]] = {}
    for span in spans:
        groups.setdefault(span.speaker, []).append(span)
    if len(groups) < 2:
        return spans

    limit = float(settings.get("speaker_merge_similarity", 0.78))
    # Порог «обрывка» растёт вместе с записью: полминуты речи на часовой
    # встрече — это почти наверняка огрехи разделения, а на трёхминутной
    # заметке — полноценный участник.
    duration = audio.size / media.SAMPLE_RATE
    tiny = max(float(settings.get("min_speaker_seconds", 2.0)),
               duration * float(settings.get("min_speaker_share", 0.01)))
    if limit >= 1.0:
        return spans

    if progress:
        progress(0.0, "Сверяю голоса между собой")
    try:
        extractor = embedder(int(settings["num_threads"]))
        prints = {key: voice_print(extractor, audio, items)
                  for key, items in groups.items()}
    except Exception:
        return spans

    # Кластеры, где вся речь короче секунды, отпечатка не дают — их разложим
    # по соседям во времени.
    speechless = [k for k, v in prints.items() if v is None]
    prints = {k: v for k, v in prints.items() if v is not None}
    similarity = {(a, b): float(prints[a] @ prints[b])
                  for a in prints for b in prints if a < b}

    def pair_value(a: int, b: int) -> float:
        return similarity[(a, b)] if a < b else similarity[(b, a)]

    def linkage(one: list[int], other: list[int]) -> float:
        return min(pair_value(a, b) for a in one for b in other)

    bunches: list[list[int]] = [[k] for k in sorted(prints)]
    while len(bunches) > 1:
        best, pair = limit, None
        for i in range(len(bunches)):
            for j in range(i + 1, len(bunches)):
                value = linkage(bunches[i], bunches[j])
                if value >= best:
                    best, pair = value, (i, j)
        if pair is None:
            break
        i, j = pair
        bunches[i] += bunches.pop(j)

    seconds = {k: sum(s.end - s.start for s in groups[k]) for k in groups}

    def bunch_seconds(bunch: list[int]) -> float:
        return sum(seconds[k] for k in bunch)

    # Обрывки: если голос звучал совсем мало, он почти всегда чужой хвост.
    bunches.sort(key=bunch_seconds)
    while len(bunches) > 1 and bunch_seconds(bunches[0]) < tiny:
        small = bunches.pop(0)
        nearest = max(range(len(bunches)),
                      key=lambda n: max(pair_value(a, b)
                                        for a in small for b in bunches[n]))
        bunches[nearest] += small
        bunches.sort(key=bunch_seconds)

    owner: dict[int, int] = {}
    for number, bunch in enumerate(bunches):
        for key in bunch:
            owner[key] = number

    # Кластеры без отпечатка отдаём тому, кто говорил рядом по времени.
    for key in speechless:
        for span in groups[key]:
            middle = (span.start + span.end) / 2
            neighbour, gap = None, float("inf")
            for other, chunk in groups.items():
                if other not in owner:
                    continue
                for item in chunk:
                    distance = 0.0 if item.start <= middle <= item.end else min(
                        abs(item.start - middle), abs(item.end - middle))
                    if distance < gap:
                        neighbour, gap = owner[other], distance
            if neighbour is not None:
                owner.setdefault(key, neighbour)

    if not owner:
        return spans
    out = [SpeakerSpan(s.start, s.end, owner.get(s.speaker, s.speaker)) for s in spans]
    out = _renumber(out)
    if progress:
        progress(1.0, f"Голосов после сверки: {len({s.speaker for s in out})}")
    return out


def _renumber(spans: list[SpeakerSpan]) -> list[SpeakerSpan]:
    """Кластеры приходят с произвольными номерами — перенумеровываем по времени
    появления, чтобы «Спикер 1» был тем, кто заговорил первым."""
    mapping: dict[int, int] = {}
    for s in spans:
        if s.speaker not in mapping:
            mapping[s.speaker] = len(mapping)
    return [SpeakerSpan(s.start, s.end, mapping[s.speaker]) for s in spans]


def speaking_time(spans: list[SpeakerSpan]) -> dict[int, float]:
    totals: dict[int, float] = {}
    for s in spans:
        totals[s.speaker] = totals.get(s.speaker, 0.0) + max(0.0, s.end - s.start)
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))
