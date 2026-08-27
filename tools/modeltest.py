"""Сравнение моделей и настроек на своей настоящей записи.

Обзоры моделей меряют код и математику на видеокартах — про то, как модель
сводит ваш русский созвон на вашем маке, там нет ничего. Поэтому: берём уже
разобранную запись, гоняем по её расшифровке несколько моделей и кладём ответы
рядом, вместе со временем работы и мерой конкретики.

Конкретику меряем так: из расшифровки вынимаются все прозвучавшие числа и
сроки, а потом считается, сколько из них дошло до саммари. Это не «качество»
целиком, но именно то, что теряется первым при обобщении.

Запуск:
    python tools/modeltest.py "~/Documents/Расшифровка записей/Созвон.result.json" \\
        gemma4:12b-mlx qwen3.5:9b-mlx

    # то же самое, но одной моделью с разными настройками
    python tools/modeltest.py <файл> qwen3.5:9b-mlx --variants
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import merge, summarize  # noqa: E402
from app.settings import Settings  # noqa: E402

OUT_DIR = Path("/tmp/modeltest")

# Наборы настроек для проверки «а если покрутить ручки».
VARIANTS = {
    "по частям": {"summary_chunk_chars": 24000, "llm_max_tokens": 6000,
                  "summary_thorough": False},
    "по частям+проверка": {"summary_chunk_chars": 24000, "llm_max_tokens": 6000,
                           "summary_thorough": True},
    "мелкими частями": {"summary_chunk_chars": 12000, "llm_max_tokens": 6000,
                        "summary_thorough": False},
    "мелкими+проверка": {"summary_chunk_chars": 12000, "llm_max_tokens": 6000,
                         "summary_thorough": True},
}

# Что считаем конкретикой: числа и слова, которыми называют срок.
NUMBER = re.compile(r"\d[\d\s   ]*(?:[.,]\d+)?%?")
WHEN = ("завтра", "послезавтра", "сегодня", "понедельник", "вторник", "среду",
        "четверг", "пятниц", "выходны", "следующей неделе", "конца недели",
        "конца месяца", "квартал")


def facts(text: str) -> set[str]:
    """Числа и сроки, прозвучавшие в тексте."""
    out = {re.sub(r"[\s   ]", "", m.group(0)) for m in NUMBER.finditer(text or "")}
    out = {x for x in out if len(x) >= 2}          # одиночные цифры — шум
    lowered = (text or "").lower()
    out |= {word for word in WHEN if word in lowered}
    return out


def deadlines(document: str) -> tuple[int, int]:
    """Сколько строк в таблицах со сроком и сколько всего строк."""
    filled = total = 0
    for line in (document or "").split("\n"):
        if not line.strip().startswith("|") or set(line.strip()) <= set("-:| "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower().startswith(("задача", "**итого")):
            continue
        total += 1
        if cells[-1] and cells[-1] not in {"—", "-", "–", ""}:
            filled += 1
    return filled, total


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

    source_facts = facts(" ".join(t.text for t in turns))
    print(f"  в расшифровке чисел и сроков: {len(source_facts)}\n")

    runs = [(model, {}) for model in models]
    if "--variants" in sys.argv:
        runs = [(models[0], dict(options, __name__=name))
                for name, options in VARIANTS.items()]

    rows = []
    for model, options in runs:
        label = options.pop("__name__", model)
        print(f"— {label}: считаю…", flush=True)
        probe = Settings({**settings, "llm_backend": "ollama", "ollama_model": model,
                          **options})
        started = time.time()
        try:
            summary = summarize.summarize(turns, probe, meta=meta, names=names_of(data))
            spent = time.time() - started
            body = summary.markdown
            note = ""
        except Exception as exc:
            spent = time.time() - started
            body, note = "", str(exc)[:120]

        kept = len(source_facts & facts(body)) if body else 0
        filled, total = deadlines(body)
        target = OUT_DIR / f"{label.replace(':', '_').replace('/', '_').replace(' ', '-')}.md"
        target.write_text(body or f"(ошибка: {note})", "utf-8")
        rows.append((label, spent, len(body), kept, filled, total, note, target))
        print(f"  {spent:.0f} с, {len(body)} знаков, конкретики {kept}, "
              f"сроков в таблицах {filled} из {total}"
              f"{' — ' + note if note else ''}\n")

    print("Итого:")
    print(f"  {'вариант':22} {'время':>7} {'знаков':>8} {'конкретики':>11} {'сроков':>9}")
    for label, spent, size, kept, filled, total, note, _ in rows:
        print(f"  {label:22} {spent:>6.0f}с {size:>8} {kept:>11} {f'{filled}/{total}':>9}"
              f"{'  ' + note if note else ''}")
    print("\nОтветы целиком:")
    for row in rows:
        print(f"  {row[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
