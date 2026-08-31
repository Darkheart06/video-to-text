"""Разделение по спикерам (диаризация) через sherpa-onnx. Работает офлайн."""

from __future__ import annotations

import shutil
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import i18n, media
from .settings import MODELS_DIR, Settings

SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
# Модели голосовых отпечатков. По ним приложение делает две вещи: сводит
# кластеры одного человека (`refine`) и узнаёт знакомые голоса. Чем точнее
# отпечаток, тем меньше и «Спикеров 6» из одного человека, и чужих имён.
# Все ссылки — из открытых выпусков sherpa-onnx: качаются без токенов и
# принятия лицензий, иначе приложение нельзя было бы просто отдать коллеге.
EMB_BASE = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-recongition-models/")
EMB_MODELS: dict[str, tuple[str, str]] = {
    # ключ: (имя файла в выпуске, имя файла на диске)
    "campp": ("wespeaker_en_voxceleb_CAM%2B%2B.onnx",
              "wespeaker_en_voxceleb_CAMPP.onnx"),
    "resnet293": ("wespeaker_en_voxceleb_resnet293_LM.onnx",
                  "wespeaker_en_voxceleb_resnet293_LM.onnx"),
    "resnet152": ("wespeaker_en_voxceleb_resnet152_LM.onnx",
                  "wespeaker_en_voxceleb_resnet152_LM.onnx"),
    "eres2netv2": ("3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx",
                   "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx"),
    "titanet": ("nemo_en_titanet_large.onnx", "nemo_en_titanet_large.onnx"),
}
DEFAULT_EMB = "campp"

SEG_PATH = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"


def emb_url(key: str) -> str:
    return EMB_BASE + EMB_MODELS.get(key, EMB_MODELS[DEFAULT_EMB])[0]


def emb_path(key: str = DEFAULT_EMB) -> Path:
    return MODELS_DIR / EMB_MODELS.get(key, EMB_MODELS[DEFAULT_EMB])[1]


def emb_choice(settings: Settings | None = None) -> str:
    """Какая модель отпечатков выбрана. Незнакомое имя — молча к обычной:
    настройка не повод остаться без разделения по голосам вовсе."""
    key = str((settings or {}).get("voice_model", "") or DEFAULT_EMB)
    return key if key in EMB_MODELS else DEFAULT_EMB


EMB_URL = emb_url(DEFAULT_EMB)
EMB_PATH = emb_path(DEFAULT_EMB)

Progress = Callable[[float, str], None]


class DiarizationError(RuntimeError):
    pass


@dataclass
class SpeakerSpan:
    start: float
    end: float
    speaker: int


def models_ready(key: str = DEFAULT_EMB) -> bool:
    return SEG_PATH.exists() and emb_path(key).exists()


def download_models(progress: Progress | None = None, key: str = DEFAULT_EMB) -> None:
    """Скачивает модели диаризации (~35 МБ) в папку models/."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    if not SEG_PATH.exists():
        report(0.0, i18n.t("diar.download_seg"))
        tmp = MODELS_DIR / "segmentation.tar.bz2"
        _download(SEG_URL, tmp,
                  lambda f: report(f * 0.3, i18n.t("diar.download_seg")))
        with tarfile.open(tmp, "r:bz2") as tf:
            try:
                tf.extractall(MODELS_DIR, filter="data")  # Python 3.12+
            except TypeError:
                tf.extractall(MODELS_DIR)
        tmp.unlink(missing_ok=True)
        if not SEG_PATH.exists():
            raise DiarizationError(i18n.t("err.seg_archive"))

    target = emb_path(key)
    if not target.exists():
        report(0.3, i18n.t("diar.download_emb"))
        _download(emb_url(key), target,
                  lambda f: report(0.3 + f * 0.7, i18n.t("diar.download_emb")))

    report(1.0, i18n.t("diar.models_ready"))


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
        raise DiarizationError(i18n.t("err.sherpa")) from exc

    key = emb_choice(settings)
    if not models_ready(key):
        download_models(progress, key)

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
            model=str(emb_path(key)), num_threads=threads, provider="cpu"
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
        raise DiarizationError(i18n.t("err.sample_rate", model=engine.sample_rate,
                                      audio=media.SAMPLE_RATE))

    if progress:
        progress(0.0, i18n.t("diar.run"))

    def callback(processed: int, total: int) -> int:
        if progress and total:
            progress(min(0.99, processed / total), i18n.t("diar.run"))
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
            progress(1.0, i18n.t("diar.found", n=len({s.speaker for s in spans})))
        return spans
    spans = refine(spans, audio, settings, progress)
    if progress:
        progress(1.0, i18n.t("diar.found", n=len({s.speaker for s in spans})))
    return spans


# --- сведение одного человека, разбитого на несколько голосов ---------------

def embedder(threads: int, key: str = DEFAULT_EMB):
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(emb_path(key)), num_threads=threads, provider="cpu"
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


def halves(spans: list[SpeakerSpan]) -> tuple[list[SpeakerSpan], list[SpeakerSpan]]:
    """Делит речь одного голоса на две половины примерно поровну.

    Не «первая половина встречи и вторая», а вперемешку: если в кластер попали
    два человека, при делении по времени половины окажутся непохожими, и мы
    сочтём, что в этой записи даже один человек сам на себя не похож. При
    чередовании оба попадут в обе половины, и ошибка уйдёт в безопасную
    сторону — порог получится строже, а не мягче.
    """
    one: list[SpeakerSpan] = []
    other: list[SpeakerSpan] = []
    for n, span in enumerate(sorted(spans, key=lambda s: -(s.end - s.start))):
        (one if n % 2 == 0 else other).append(span)
    return one, other


def self_similarity(extractor, audio: np.ndarray,
                    groups: dict[int, list[SpeakerSpan]],
                    minimum: float = 12.0) -> list[float]:
    """Насколько человек похож сам на себя — в этой самой записи.

    Микрофон, комната, связь и то, как человек говорит именно сегодня, сдвигают
    похожесть целиком: на одной записи два куска речи одного человека дают 0.95,
    на другой — 0.7. Поэтому меряем это прямо здесь: режем речь каждого голоса
    пополам и сравниваем половины между собой.
    """
    out: list[float] = []
    for items in groups.values():
        if sum(s.end - s.start for s in items) < minimum:
            continue
        one, other = halves(items)
        first = voice_print(extractor, audio, one, seconds=30.0)
        second = voice_print(extractor, audio, other, seconds=30.0)
        if first is None or second is None:
            continue
        out.append(float(first @ second))
    return out


def auto_limit(values: list[float], settings) -> float | None:
    """Порог сведения, посчитанный по самой записи.

    «Сам на себя» — это заодно и оценка того, насколько вообще можно доверять
    сравнению голосов в этой записи. Высокая (0.95) — отпечатки устойчивые, и
    порог можно поднять почти к ней: настоящий один человек, разорванный на два
    кластера, всё равно наберёт больше. Низкая (0.7) — короткие реплики, плохой
    микрофон, отпечатки шумят; в такой записи разные люди легко набирают 0.8, и
    смело сводить голоса нельзя.

    Поэтому порог только растёт: ниже настроенного числа он не опускается. Так
    сведение в худшем случае работает как раньше, а на хорошей записи —
    аккуратнее. Берём не среднее, а нижнюю четверть: порог должен пережить
    самого «неровного» из говорящих, иначе его разорвёт надвое.
    """
    if len(values) < 2:
        return None
    ordered = sorted(values)
    base = ordered[round(0.25 * (len(ordered) - 1))]
    step = float(settings.get("speaker_merge_margin", 0.06))
    fixed = float(settings.get("speaker_merge_similarity", 0.78))
    return min(0.92, max(fixed, base - step))


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

    Насколько «похоже» — считаем по самой записи (см. self_similarity), а не
    берём число из настроек: на одной записи два куска речи одного человека
    похожи на 0.95, на другой — на 0.7, и один и тот же порог там окажется то
    слишком строгим, то слишком мягким.

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
        progress(0.0, i18n.t("diar.compare"))
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

    # Порог берём из самой записи, а не из настроек: см. self_similarity.
    if settings.get("speaker_merge_auto", True) and len(prints) > 1:
        try:
            measured = self_similarity(extractor, audio,
                                       {k: groups[k] for k in prints})
        except Exception:
            measured = []
        found = auto_limit(measured, settings)
        if found is not None:
            limit = found
            if progress:
                progress(0.5, i18n.t("diar.limit", value=f"{limit:.2f}"))

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
        progress(1.0, i18n.t("diar.after", n=len({s.speaker for s in out})))
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
