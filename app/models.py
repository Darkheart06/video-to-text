"""Какие модели распознавания уже лежат на диске.

Своя модель раньше вписывалась руками — именем репозитория. Так работает, но
требует помнить его наизусть и знать, что вообще скачано. Здесь тот же список
собирается сам: то, что уже скачано в кэш Hugging Face, и то, что положено в
папку `models/` рядом с приложением.

Заодно проверяется, чем модель вообще можно открыть. Формата три, и они не
взаимозаменяемы:

* **mlx** — то, чем работает Apple Silicon (`mlx-community/whisper-…`);
* **ct2** — CTranslate2, формат faster-whisper;
* **torch** — обычный чекпойнт transformers, каким выкладывают дообученные
  модели. Ни одним из движков приложения он не открывается: нужна конвертация
  (`tools/getmodel.py`).

Показать в списке модель, которую выбранный движок не откроет, — значит
пообещать то, чего не будет. Поэтому формат виден в самом списке.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .settings import MODELS_DIR

# Больше этого не сканируем: кэш Hugging Face бывает на десятки папок, а искать
# в нём нужно только модели распознавания.
MAX_FOUND = 40


@dataclass
class Found:
    id: str          # что подставить в настройку: имя репозитория или путь
    name: str        # как показать человеку
    kind: str        # mlx | ct2 | torch
    size: int        # байт
    where: str       # cache | models

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "size": self.size, "where": self.where}


def hf_cache() -> Path:
    """Папка кэша Hugging Face — с оглядкой на переменные окружения."""
    for name in ("HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _size(folder: Path) -> int:
    total = 0
    try:
        for item in folder.rglob("*"):
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
            elif item.is_symlink():
                # В кэше Hugging Face файлы — ссылки на blobs; считаем их тоже,
                # иначе всякая модель выглядит невесомой.
                try:
                    total += item.resolve().stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def kind_of(folder: Path) -> str:
    """Чем эту папку можно открыть. Пусто — ничем из знакомого."""
    names = {item.name for item in folder.iterdir() if item.is_file() or item.is_symlink()} \
        if folder.is_dir() else set()
    if not names:
        return ""
    if "weights.npz" in names or "weights.safetensors" in names:
        return "mlx"
    if "model.bin" in names and any(n.startswith("vocabulary") for n in names):
        return "ct2"
    if {"model.safetensors", "pytorch_model.bin"} & names:
        # У MLX встречается и safetensors — отличаем по составу: у transformers
        # рядом лежит препроцессор, у MLX его нет.
        if "preprocessor_config.json" in names or "tokenizer.json" in names:
            return "torch"
        return "mlx"
    return ""


def _is_whisper(folder: Path, hint: str) -> bool:
    if "whisper" in hint.lower():
        return True
    config = folder / "config.json"
    if not config.exists():
        return False
    try:
        data = json.loads(config.read_text("utf-8"))
    except Exception:
        return False
    return (str(data.get("model_type", "")).lower() == "whisper"
            or "num_mel_bins" in data or "n_mels" in data)


def _snapshot(folder: Path) -> Path | None:
    """Последний снимок репозитория в кэше Hugging Face."""
    snaps = folder / "snapshots"
    if not snaps.is_dir():
        return None
    kids = [item for item in snaps.iterdir() if item.is_dir()]
    if not kids:
        return None
    return max(kids, key=lambda item: item.stat().st_mtime)


def installed() -> list[dict]:
    """Все модели распознавания, которые уже есть на этой машине."""
    found: list[Found] = []

    cache = hf_cache()
    if cache.is_dir():
        for folder in sorted(cache.iterdir()):
            if not folder.name.startswith("models--"):
                continue
            repo = folder.name[len("models--"):].replace("--", "/")
            snapshot = _snapshot(folder)
            if snapshot is None or not _is_whisper(snapshot, repo):
                continue
            kind = kind_of(snapshot)
            if not kind:
                continue
            found.append(Found(id=repo, name=repo, kind=kind,
                               size=_size(folder), where="cache"))

    if MODELS_DIR.is_dir():
        for folder in sorted(MODELS_DIR.iterdir()):
            if not folder.is_dir() or not _is_whisper(folder, folder.name):
                continue
            kind = kind_of(folder)
            if not kind:
                continue
            found.append(Found(id=str(folder), name=folder.name, kind=kind,
                               size=_size(folder), where="models"))

    # Сначала то, что лежит рядом с приложением: это положили руками, а значит
    # скорее всего ради него сюда и пришли.
    found.sort(key=lambda item: (item.where != "models", item.name.lower()))
    return [item.as_dict() for item in found[:MAX_FOUND]]


def usable(kind: str, backend: str) -> bool:
    """Откроет ли этот движок такую модель."""
    if backend == "mlx":
        return kind == "mlx"
    if backend in ("faster", "faster-whisper"):
        return kind == "ct2"
    return kind in ("mlx", "ct2")
