#!/usr/bin/env python3
"""Скачать модель распознавания и, если нужно, перегнать в рабочий формат.

Дообученные модели выкладывают чекпойнтом transformers, а движки приложения
такой не открывают: MLX работает только со своим форматом, faster-whisper — с
CTranslate2. Поэтому мало скачать, нужно ещё и перегнать.

    ./getmodel.sh antony66/whisper-large-v3-russian
    ./getmodel.sh mlx-community/whisper-large-v3-turbo     # уже в формате MLX
    ./getmodel.sh --list                                   # что уже есть

То же самое умеет и само окно: в настройках рядом со списком своих моделей
стоит кнопка. Этот скрипт — для тех, кому привычнее из терминала; работа идёт
одним и тем же кодом (`app/models.py`), поэтому разойтись они не могут.

Перегнанное кладётся в `models/` рядом с приложением и появляется в списке
своих моделей в настройках.

Чего этот скрипт не делает: не переводит в формат MLX. Официального
конвертера в пакете mlx-whisper нет, а писать свой — это отдельная работа с
раскладкой весов, которую нельзя проверить без Apple Silicon. Модель в формате
MLX либо есть у mlx-community готовая, либо остаётся faster-whisper.
"""

from __future__ import annotations

import argparse
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


def note(frac: float, message: str) -> None:
    print(f"  {int(frac * 100):3d}%  {message}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Скачать модель распознавания и подготовить её к работе")
    parser.add_argument("repo", nargs="?", default="",
                        help="имя репозитория Hugging Face")
    parser.add_argument("--list", action="store_true", help="что уже скачано")
    args = parser.parse_args()

    if args.list or not args.repo:
        return show_found()

    # Предупреждаем до скачивания: узнать про недостающий конвертер после
    # трёх гигабайт загрузки — обидно.
    lack = models.converter_missing()
    if lack:
        print(f"Внимание: для конвертации нужны {' и '.join(lack)} "
              f"(около 3 ГБ, ставятся один раз).\n"
              f"Поставить: .venv/bin/pip install {' '.join(lack)}\n"
              "Или нажмите «Скачать и поставить» в настройках приложения.\n"
              "Если модель окажется в готовом формате, конвертация не понадобится.\n")

    try:
        done = models.prepare(args.repo, note)
    except Exception as exc:
        raise SystemExit(str(exc)) from None

    if not done.get("ok"):
        raise SystemExit(
            "скачано, но перегнать нечем: не хватает "
            + " и ".join(done.get("need") or []))
    if done.get("converted"):
        print(f"\nГотово: {done['id']}")
        print("Модель появилась в списке своих моделей в настройках.")
        print("Она в формате CTranslate2 — выберите движок «faster-whisper»: "
              "MLX такой формат не открывает.")
    else:
        print(f"\nГотово, формат {done['kind']} — конвертация не нужна.")
        print(f"Выберите в настройках «своя модель» → {done['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
