"""Правка саммари вручную.

Модель иногда тащит в бриф то, что к делу не относится: обсуждение погоды,
случайный кусок чужого разговора, задачу, которой никто не ставил. Выкидывать
это из расшифровки нельзя — там должно остаться сказанное как есть, — а вот из
саммари и списка задач нужно.

Здесь правится только разобранный документ: разделы, таблицы, итоги. Файлы
транскрипта и субтитров не трогаются вообще.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import compute, render

RESULT_SUFFIX = ".result.json"


class EditError(RuntimeError):
    pass


def apply(result_path: str | Path, key: str, markdown: str) -> dict:
    """Заменяет один раздел саммари и переписывает файлы записи.

    Возвращает обновлённые разделы и весь документ — интерфейсу этого хватает,
    чтобы перерисоваться, не перечитывая файл.
    """
    path = Path(result_path)
    if not path.exists() or not path.name.endswith(RESULT_SUFFIX):
        raise EditError("Файл записи не найден")

    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        raise EditError(f"Не удалось прочитать запись: {exc}") from exc

    summary = data.get("summary")
    if not summary:
        raise EditError("У этой записи нет саммари")

    sections = dict(summary.get("sections") or {})
    if key not in sections:
        raise EditError("Такого раздела в записи нет")

    tabs = [tuple(x) for x in (summary.get("tabs") or []) if len(x) == 2]
    if not tabs:
        tabs = [(k, k) for k in sections]

    sections[key] = markdown.strip()
    document = assemble(tabs, sections)

    # Строку из сметы могли удалить — итог пересчитываем, иначе он будет врать.
    result = compute.process(document, str((data.get("meta") or {}).get("title", "")))
    document = result.markdown
    sections = _split(document, tabs, sections)

    summary["sections"] = sections
    summary["markdown"] = document
    summary["edited"] = True
    data["summary"] = summary

    stem = path.name[: -len(RESULT_SUFFIX)]
    directory = path.parent
    meta = data.get("meta") or {}

    (directory / f"{stem}.summary.md").write_text(
        render.summary_markdown(_AsSummary(document), meta), "utf-8")

    csv_path = directory / f"{stem}.таблицы.csv"
    if result.tables:
        csv_path.write_text(compute.to_csv(result.tables), "utf-8")
    elif csv_path.exists():
        # Таблиц не осталось — файл со старыми суммами только запутает.
        csv_path.unlink(missing_ok=True)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    return {"sections": sections, "markdown": document,
            "tables": bool(result.tables)}


def assemble(tabs: list[tuple[str, str]], sections: dict[str, str]) -> str:
    """Собирает документ обратно из разделов, в порядке вкладок."""
    parts = []
    for key, title in tabs:
        body = (sections.get(key) or "").strip()
        if not body:
            continue
        parts.append(f"## {title}\n{body}")
    for key, body in sections.items():
        if key not in {k for k, _ in tabs} and body.strip():
            parts.append(f"## {key}\n{body.strip()}")
    return "\n\n".join(parts) + "\n"


def _split(document: str, tabs: list[tuple[str, str]],
           previous: dict[str, str]) -> dict[str, str]:
    """Разбирает документ обратно по ключам разделов.

    Пересчёт таблиц мог поменять текст, поэтому после него разделы читаются
    заново — и по тем же заголовкам, что мы сами и написали.
    """
    from . import summarize

    fresh = summarize.parse_sections(document, tabs)
    return {key: fresh.get(key, previous.get(key, "")) for key, _ in tabs}


class _AsSummary:
    """render.summary_markdown ждёт объект с полем markdown — больше ему
    ничего не нужно."""

    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
