"""Настройки приложения. Хранятся в config.json рядом с проектом."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
MODELS_DIR = ROOT / "models"
WORK_DIR = ROOT / ".work"

# Результаты кладём туда, где человек их найдёт, а не внутрь приложения:
# установленная копия живёт в Library, куда никто не заглядывает.
OUTPUT_DIR = Path.home() / "Documents" / "Расшифровка записей"

# Модели Whisper: короткое имя -> (репозиторий mlx, имя для faster-whisper)
WHISPER_MODELS = {
    "large-v3-turbo": ("mlx-community/whisper-large-v3-turbo", "large-v3-turbo"),
    "large-v3": ("mlx-community/whisper-large-v3-mlx", "large-v3"),
    "medium": ("mlx-community/whisper-medium-mlx", "medium"),
    "small": ("mlx-community/whisper-small-mlx", "small"),
    "base": ("mlx-community/whisper-base-mlx", "base"),
}

DEFAULTS = {
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
    "min_speaker_seconds": 2.0,         # обрывки короче отдаём ближайшему голосу
    "min_speaker_share": 0.01,          # …как и тех, кто занял меньше доли записи

    # Саммари: что готовим и какая языковая модель это делает
    "summary_enabled": True,
    "preset": "meeting",             # meeting | estimate | note | interview | custom
    "custom_rules": "",              # свои правила для модели
    "custom_template": "",           # свой шаблон документа (заголовки ##)
    "llm_backend": "auto",           # auto | ollama | gguf | openai
    "llm_num_ctx": 32768,
    "summary_chunk_chars": 24000,

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
    def output_path(self) -> Path:
        p = Path(self["output_dir"]).expanduser() if self["output_dir"] else OUTPUT_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

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
