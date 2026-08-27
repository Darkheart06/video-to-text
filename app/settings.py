"""Настройки приложения. Хранятся в config.json рядом с проектом."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

from . import i18n

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
MODELS_DIR = ROOT / "models"
WORK_DIR = ROOT / ".work"

# Результаты кладём туда, где человек их найдёт, а не внутрь приложения:
# установленная копия живёт в Library, куда никто не заглядывает.
DOCUMENTS = Path.home() / "Documents"


def output_dir_for(lang: str) -> Path:
    return DOCUMENTS / i18n.d("out.dir", lang)


# Папка по умолчанию — на языке документов; старое русское имя остаётся
# рабочим, архив читает обе.
OUTPUT_DIR = output_dir_for("ru")

# Модели Whisper: короткое имя -> (репозиторий mlx, имя для faster-whisper)
WHISPER_MODELS = {
    "large-v3-turbo": ("mlx-community/whisper-large-v3-turbo", "large-v3-turbo"),
    "large-v3": ("mlx-community/whisper-large-v3-mlx", "large-v3"),
    "medium": ("mlx-community/whisper-medium-mlx", "medium"),
    "small": ("mlx-community/whisper-small-mlx", "small"),
    "base": ("mlx-community/whisper-base-mlx", "base"),
}

DEFAULTS = {
    # Язык окна и язык документов — вещи разные: окно бывает английским у
    # человека, который сводит русские созвоны. «auto» у окна — язык системы,
    # «auto» у документов — вслед за окном.
    "ui_language": "auto",           # auto | en | ru
    "doc_language": "auto",          # auto | en | ru

    # Распознавание речи
    "asr_backend": "auto",           # auto | mlx | faster
    "whisper_model": "large-v3-turbo",
    "language": "auto",              # auto | ru | en | ...
    "compute_type": "int8",          # только для faster-whisper
    "chunk_seconds": 600,            # длина куска для mlx-бэкенда (прогресс + память)

    # Диаризация (разделение по спикерам)
    "diarization_enabled": True,
    "num_speakers": 0,               # 0 = определить автоматически
    "cluster_threshold": 0.6,        # меньше -> больше спикеров
    "min_duration_on": 0.3,
    "min_duration_off": 0.5,
    # Кластеризация дробит одного человека на несколько голосов — после неё
    # отпечатки сверяются между собой, и слишком похожие сводятся в один.
    "speaker_merge_similarity": 0.78,   # 1.0 = не сводить вовсе
    # Похожесть голосов зависит от записи целиком: микрофон, комната, связь.
    # Поэтому порог считается по ней самой — речь каждого голоса режется
    # пополам, и «сам на себя» становится точкой отсчёта. Порог от этого только
    # растёт: ниже числа выше он не опускается, так что хуже, чем с
    # фиксированным порогом, не станет.
    "speaker_merge_auto": True,
    "speaker_merge_margin": 0.06,       # насколько порог ниже «сам на себя»
    "min_speaker_seconds": 2.0,         # обрывки короче отдаём ближайшему голосу
    "min_speaker_share": 0.01,          # …как и тех, кто занял меньше доли записи

    # Саммари: что готовим и какая языковая модель это делает
    "summary_enabled": True,
    "preset": "meeting",             # meeting | estimate | note | interview | custom
    "custom_rules": "",              # свои правила для модели
    "custom_template": "",           # свой шаблон документа (заголовки ##)
    "llm_backend": "auto",           # auto | ollama | gguf | openai
    "llm_num_ctx": 32768,
    # Сколько модель пишет в ответ и сколько текста берёт за один заход.
    # Замер на 51-минутном созвоне (tools/modeltest.py, конкретика — сколько
    # прозвучавших чисел и сроков дошло до саммари):
    #   всё одним куском (60 000)  —  91 с,  8 из 27, задач 7
    #   по 24 000                  — 147 с, 12 из 27, задач 9
    #   по 12 000                  — 283 с, 14 из 27, задач 21
    #   по 12 000 + проверка       — 269 с, 15 из 27, задач 24
    # Оказалось наоборот, чем ожидалось: чем мельче куски, тем подробнее итог.
    # Выписывая заметки по фрагменту, модель вынуждена сначала извлечь
    # конкретику, а получив всё сразу — сглаживает её на ходу.
    "llm_max_tokens": 6000,
    "summary_chunk_chars": 12000,
    # Второй заход к модели: «что важное не попало в документ». Дороже по
    # времени примерно на треть, но заметно полнее.
    "summary_thorough": True,
    # Дописывать к «завтра» и «до пятницы» настоящую дату записи.
    "resolve_dates": True,

    # …через Ollama
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "auto",

    # …из файла .gguf на диске
    "gguf_path": "",
    "gguf_gpu_layers": -1,           # -1 = целиком на видеоядро (Metal)

    # …через OpenAI-совместимый сервер (LM Studio, llama-server, Jan, LocalAI)
    "openai_base_url": "http://127.0.0.1:1234/v1",
    "openai_model": "auto",
    "openai_api_key": "",

    # Запись созвонов
    "record_chunk_seconds": 30,      # как часто разбирать накопленный звук
    "record_notes_minutes": 5,       # как часто делать короткую сводку по ходу
    "record_autodetect": True,       # предлагать запись, когда занят микрофон
    "record_dedupe": True,           # убирать фразу, попавшую в обе дорожки
    "record_split_speakers": True,   # после звонка делить собеседников по голосам
    # Узнавание отмеченного голоса: важен не сам уровень похожести, а разрыв
    # между лучшим и вторым — короткие реплики дают низкие абсолютные числа.
    "voice_match_floor": 0.35,       # ниже этого не считаем узнаванием вовсе
    "voice_match_margin": 0.07,      # на столько лучший должен опережать второго

    # Чистка транскрипта
    "transcript_cleanup": True,      # убирать эканье, паразитов и повторы

    # Прочее
    "output_dir": "",                # пусто = <проект>/output
    "keep_wav": False,
    "num_threads": 0,                # 0 = по числу ядер
}


def _default_threads() -> int:
    try:
        n = os.cpu_count() or 4
    except Exception:
        n = 4
    return max(2, min(8, n - 1))


class Settings(dict):
    """Словарь настроек с загрузкой/сохранением на диск."""

    @classmethod
    def load(cls) -> Settings:
        data = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                data.update(json.loads(CONFIG_PATH.read_text("utf-8")))
            except Exception:
                pass
        s = cls(data)
        if not s["num_threads"]:
            s["num_threads"] = _default_threads()
        return s

    def save(self) -> None:
        clean = {k: v for k, v in self.items() if k in DEFAULTS}
        CONFIG_PATH.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), "utf-8"
        )

    # --- производные значения -------------------------------------------------

    @property
    def ui_lang(self) -> str:
        return i18n.pick(self.get("ui_language", "auto"))

    @property
    def doc_lang(self) -> str:
        """Язык документов: «auto» — тот же, что у окна."""
        return i18n.pick(self.get("doc_language", "auto"), self.ui_lang)

    @property
    def output_path(self) -> Path:
        if self["output_dir"]:
            p = Path(self["output_dir"]).expanduser()
        else:
            p = output_dir_for(self.doc_lang)
            # Если человек уже накопил записи в папке с другим именем, а новая
            # ещё пуста, продолжаем складывать туда же: разъезжаться архиву ни
            # к чему.
            empty = not p.exists() or not any(p.glob("*.result.json"))
            if empty:
                for lang in i18n.LANGUAGES:
                    other = output_dir_for(lang)
                    if other != p and other.exists() and any(other.glob("*.result.json")):
                        p = other
                        break
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def library_paths(self) -> list[Path]:
        """Все папки, где могут лежать разобранные записи."""
        found = [self.output_path]
        for lang in i18n.LANGUAGES:
            other = output_dir_for(lang)
            if other not in found and other.exists():
                found.append(other)
        return found

    def whisper_repo(self, backend: str) -> str:
        mlx_repo, fw_name = WHISPER_MODELS.get(
            self["whisper_model"], WHISPER_MODELS["large-v3-turbo"]
        )
        return mlx_repo if backend == "mlx" else fw_name


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64")


def resolve_asr_backend(preference: str = "auto") -> str:
    """Возвращает 'mlx' или 'faster' в зависимости от того, что установлено."""

    def has(mod: str) -> bool:
        import importlib.util

        return importlib.util.find_spec(mod) is not None

    if preference == "mlx":
        return "mlx"
    if preference == "faster":
        return "faster"
    if is_apple_silicon() and has("mlx_whisper"):
        return "mlx"
    if has("faster_whisper"):
        return "faster"
    if has("mlx_whisper"):
        return "mlx"
    raise RuntimeError(
        "Не найден движок распознавания. Установите mlx-whisper или faster-whisper."
    )
