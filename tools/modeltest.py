"""Сравнение языковых моделей на своей настоящей записи.

Обзоры моделей меряют код и математику на видеокартах — про то, как модель
сводит ваш русский созвон на вашем маке, там нет ничего. Поэтому: берём уже
разобранную запись, гоняем по её расшифровке несколько моделей и кладём ответы
рядом, вместе со временем работы.

Запуск:
    python tools/modeltest.py "~/Documents/Расшифровка записей/Созвон.result.json" \\
        gemma4:12b-mlx qwen3.5:9b-mlx
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import merge, summarize  # noqa: E402
from app.settings import Settings  # noqa: E402

OUT_DIR = Path("/tmp/modeltest")


def turns_of(data: dict) -> list[merge.Turn]:
    """Собирает реплики из result.json — расшифровка уже есть, ASR не нужен."""
    out = []
    for item in data.get("turns") or []:
        key = str(item.get("speaker") or "")
        number = int(key[1:]) - 1 if key.startswith("S") and key[1:].isdigit() else None
        out.append(merge.Turn(start=float(item.get("start") or 0),
                              end=float(item.get("end") or 0),
                              speaker=number, text=str(item.get("text") or "")))
    return out


def names_of(data: dict) -> dict[str, str]:
    return {key: value.get("label", key)
            for key, value in (data.get("speakers") or {}).items()}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    path = Path(sys.argv[1]).expanduser()
    models = sys.argv[2:]
    if not path.exists():
        print(f"Не нашёл файл: {path}")
        return 2

    data = json.loads(path.read_text("utf-8"))
    turns = turns_of(data)
    meta = data.get("meta") or {}
    if not turns:
        print("В записи нет реплик")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    settings = Settings.load()
    letters = sum(len(t.text) for t in turns)
    print(f"Запись: {meta.get('title', path.stem)}")
    print(f"  {len(turns)} реплик, {letters} знаков, "
          f"{float(meta.get('duration', 0)) / 60:.0f} мин разговора\n")

    rows = []
    for model in models:
        print(f"— {model}: считаю…", flush=True)
        probe = Settings({**settings, "llm_backend": "ollama", "ollama_model": model})
        started = time.time()
        try:
            summary = summarize.summarize(turns, probe, meta=meta, names=names_of(data))
            spent = time.time() - started
            body = summary.markdown
            note = ""
        except Exception as exc:
            spent = time.time() - started
            body, note = "", str(exc)[:120]

        target = OUT_DIR / f"{model.replace(':', '_').replace('/', '_')}.md"
        target.write_text(body or f"(ошибка: {note})", "utf-8")
        rows.append((model, spent, len(body), len(summarize.parse_sections(body)) if body else 0,
                     note, target))
        print(f"  {spent:.0f} с, {len(body)} знаков{' — ' + note if note else ''}\n")

    print("Итого:")
    print(f"  {'модель':22} {'время':>8} {'знаков':>8} {'разделов':>9}")
    for model, spent, size, sections, note, _ in rows:
        print(f"  {model:22} {spent:>7.0f}с {size:>8} {sections:>9}"
              f"{'  ' + note if note else ''}")
    print("\nОтветы целиком:")
    for _, _, _, _, _, target in rows:
        print(f"  {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
