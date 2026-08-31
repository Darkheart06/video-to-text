#!/usr/bin/env python3
"""Скачать модель распознавания и, если нужно, перегнать в рабочий формат.

Дообученные модели выкладывают чекпойнтом transformers, а движки приложения
такой не открывают: MLX работает только со своим форматом, faster-whisper — с
CTranslate2. Поэтому мало скачать, нужно ещё и перегнать.

    ./getmodel.sh antony66/whisper-large-v3-russian
    ./getmodel.sh mlx-community/whisper-large-v3-turbo     # уже в формате MLX
    ./getmodel.sh --list                                   # что уже есть

Перегнанное кладётся в `models/` рядом с приложением и появляется в списке
своих моделей в настройках. Конвертация идёт через `ct2-transformers-converter`
из ctranslate2 — он ставится вместе с faster-whisper, отдельно ничего не нужно.

Чего этот скрипт не делает: не переводит в формат MLX. Официального
конвертера в пакете mlx-whisper нет, а писать свой — это отдельная работа с
раскладкой весов, которую нельзя проверить без Apple Silicon. Модель в формате
MLX либо есть у mlx-community готовая, либо остаётся faster-whisper.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import models  # noqa: E402
from app.settings import MODELS_DIR  # noqa: E402


def show_found() -> int:
    rows = models.installed()
    if not rows:
        print("Скачанных моделей распознавания не нашлось.")
        print(f"Кэш Hugging Face: {models.hf_cache()}")
        print(f"Папка приложения: {MODELS_DIR}")
        return 0
    print("Модели распознавания на этой машине:")
    for row in rows:
        gb = row["size"] / 1e9
        size = f"{gb:.1f} ГБ" if gb >= 1 else f"{row['size'] / 1e6:.0f} МБ"
        note = {"mlx": "формат MLX", "ct2": "формат CTranslate2",
                "torch": "transformers — нужна конвертация"}.get(row["kind"], row["kind"])
        print(f"  {row['name']}\n      {note}, {size}, {row['where']}")
    return 0


def converter_ready() -> str:
    """Пусто, если перегнать есть чем. Иначе — чего не хватает.

    Конвертер живёт в ctranslate2 (он ставится с faster-whisper), но читать
    чекпойнт умеет только через transformers и torch, а их в приложении нет:
    вдвоём они весят под три гигабайта, и ставить их всем ради того, чем
    воспользуется один человек из ста, — плохая сделка. Поэтому проверяем и
    говорим, что доставить.
    """
    missing = []
    for name in ("transformers", "torch"):
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if not missing:
        return ""
    return (f"для конвертации нужны {' и '.join(missing)} — поставьте их в "
            f"окружение приложения:\n  .venv/bin/pip install {' '.join(missing)}")


def fetch(repo: str) -> Path:
    """Скачивает репозиторий в кэш Hugging Face и возвращает путь к снимку."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit(
            "нет huggingface_hub — он ставится вместе с mlx-whisper; "
            "выполните bash install.sh") from None
    print(f"Скачиваю {repo} …")
    # Веса в двух форматах качать незачем: берём safetensors, а .bin только
    # если safetensors в репозитории нет.
    path = Path(snapshot_download(repo, ignore_patterns=["*.msgpack", "*.h5", "*.ot"]))
    print(f"  готово: {path}")
    return path


def to_ct2(repo: str, source: Path, name: str = "") -> Path:
    """Перегоняет чекпойнт transformers в формат faster-whisper."""
    converter = shutil.which("ct2-transformers-converter") or \
        str(Path(sys.executable).with_name("ct2-transformers-converter"))
    if not Path(converter).exists() and not shutil.which("ct2-transformers-converter"):
        raise SystemExit("нет ct2-transformers-converter — он ставится вместе с "
                         "faster-whisper; выполните bash install.sh")
    target = MODELS_DIR / (name or (repo.replace("/", "--") + "-ct2"))
    if target.exists():
        print(f"Уже перегнано: {target}")
        return target
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Перегоняю в CTranslate2 → {target}")
    # float16 — то же, чем пользуется faster-whisper по умолчанию: вдвое
    # меньше на диске и в памяти, качество то же.
    done = subprocess.run(
        [converter, "--model", str(source), "--output_dir", str(target),
         "--copy_files", "tokenizer.json", "preprocessor_config.json",
         "--quantization", "float16"],
        capture_output=True, text=True)
    if done.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-8:]
        raise SystemExit("конвертация не вышла:\n  " + "\n  ".join(tail))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Скачать модель распознавания и подготовить её к работе")
    parser.add_argument("repo", nargs="?", default="",
                        help="имя репозитория Hugging Face")
    parser.add_argument("--list", action="store_true", help="что уже скачано")
    parser.add_argument("--name", default="", help="имя папки для перегнанной модели")
    args = parser.parse_args()

    if args.list or not args.repo:
        return show_found()

    # Предупреждаем до скачивания: узнать про недостающий конвертер после
    # трёх гигабайт загрузки — обидно.
    lack = converter_ready()
    if lack:
        print(f"Внимание: {lack}\n"
              "Если модель окажется в готовом формате, конвертация не понадобится.\n")

    source = fetch(args.repo)
    kind = models.kind_of(source)
    if kind == "mlx":
        print("Формат MLX — конвертация не нужна.")
        print(f"Выберите в настройках «своя модель» → {args.repo}")
        return 0
    if kind == "ct2":
        print("Формат CTranslate2 — конвертация не нужна.")
        print(f"Выберите в настройках «своя модель» → {args.repo}")
        return 0
    if kind != "torch":
        raise SystemExit(f"не понимаю, что это за модель: {source}")

    lack = converter_ready()
    if lack:
        raise SystemExit(lack)
    target = to_ct2(args.repo, source, args.name)
    print(f"\nГотово: {target}")
    print("Модель появилась в списке своих моделей в настройках.")
    print("Она в формате CTranslate2, поэтому в настройках выберите движок "
          "«faster-whisper»: MLX такой формат не открывает.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
