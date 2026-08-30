"""Полки: закреплённые записи и папки, по которым они разложены.

Записи лежат на диске плоским списком файлов, и это правильно: так их видно
в Finder, так они переживают переустановку. Но когда записей за сотню, плоский
список перестаёт помогать — нужное приходится искать. Поэтому поверх файлов
живёт тонкий слой: что закреплено наверху и что в какой папке.

**Папки здесь — не папки на диске.** Разложить файлы по настоящим каталогам
значило бы ломать пути, ссылки на прикреплённые документы и всё, что уже
записано в `.result.json`. Поэтому папка — это просто подпись у записи, а
дерево собирается в окне. Файлы при этом остаются там, где были, и человек
находит их в Finder привычным способом.

Ключ — тот же идентификатор, что у записи в архиве (`library._ident`, хэш от
пути). При переименовании записи путь меняется, а значит меняется и ключ:
`move()` переносит полку за записью, иначе закрепление и папка терялись бы от
каждой правки названия.
"""

from __future__ import annotations

import json
import time

from .settings import WORK_DIR

STORE = WORK_DIR / "shelf.json"

# Больше двух сотен папок — это уже не полки, а свалка: ограничение здесь
# затем, чтобы случайный цикл не записал их тысячу.
FOLDER_LIMIT = 200
NAME_LIMIT = 60


def load() -> dict:
    try:
        data = json.loads(STORE.read_text("utf-8"))
    except Exception:
        return {"folders": [], "items": {}}
    if not isinstance(data, dict):
        return {"folders": [], "items": {}}
    data.setdefault("folders", [])
    data.setdefault("items", {})
    return data


def save(data: dict) -> None:
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def folders() -> list[str]:
    """Папки в том порядке, в каком их завёл человек."""
    return [str(name) for name in load().get("folders", []) if str(name).strip()]


def add_folder(name: str) -> list[str]:
    name = (name or "").strip()[:NAME_LIMIT]
    if not name:
        return folders()
    data = load()
    names = [str(x) for x in data.get("folders", [])]
    # Сравниваем без учёта регистра: «Клиенты» и «клиенты» — одна папка,
    # иначе в дереве появляются близнецы, и человек не понимает, куда попал.
    if not any(x.lower() == name.lower() for x in names) and len(names) < FOLDER_LIMIT:
        names.append(name)
    data["folders"] = names
    save(data)
    return names


def remove_folder(name: str) -> list[str]:
    """Убирает папку. Записи из неё не пропадают — возвращаются в общий список."""
    name = (name or "").strip()
    data = load()
    data["folders"] = [x for x in data.get("folders", []) if str(x) != name]
    for key, item in list(data.get("items", {}).items()):
        if str(item.get("folder") or "") == name:
            item.pop("folder", None)
            if not item:
                data["items"].pop(key, None)
    save(data)
    return [str(x) for x in data["folders"]]


def rename_folder(name: str, fresh: str) -> list[str]:
    name, fresh = (name or "").strip(), (fresh or "").strip()[:NAME_LIMIT]
    if not name or not fresh:
        return folders()
    data = load()
    data["folders"] = [fresh if str(x) == name else str(x) for x in data.get("folders", [])]
    for item in data.get("items", {}).values():
        if str(item.get("folder") or "") == name:
            item["folder"] = fresh
    save(data)
    return [str(x) for x in data["folders"]]


def put(entry_id: str, folder: str) -> dict:
    """Кладёт запись в папку. Пустое имя — вынуть из папки."""
    folder = (folder or "").strip()[:NAME_LIMIT]
    data = load()
    item = dict(data.get("items", {}).get(entry_id) or {})
    if folder:
        item["folder"] = folder
        names = [str(x) for x in data.get("folders", [])]
        if not any(x.lower() == folder.lower() for x in names):
            names.append(folder)
            data["folders"] = names
    else:
        item.pop("folder", None)
    _write_item(data, entry_id, item)
    return item


def pin(entry_id: str, on: bool = True) -> dict:
    data = load()
    item = dict(data.get("items", {}).get(entry_id) or {})
    if on:
        # Помним время: закреплённые сортируются по нему, а не по дате записи,
        # иначе только что закреплённое уезжает в середину списка.
        item["pinned"] = time.time()
    else:
        item.pop("pinned", None)
    _write_item(data, entry_id, item)
    return item


def move(old_id: str, new_id: str) -> None:
    """Переносит полку за записью, которую переименовали."""
    if old_id == new_id:
        return
    data = load()
    item = data.get("items", {}).pop(old_id, None)
    if item:
        data["items"][new_id] = item
        save(data)


def of(entry_id: str) -> dict:
    return dict(load().get("items", {}).get(entry_id) or {})


def decorate(rows: list[dict]) -> list[dict]:
    """Дописывает записям их полку и ставит закреплённые наверх."""
    data = load()
    items = data.get("items", {})
    for row in rows:
        mark = items.get(row.get("id")) or {}
        row["folder"] = str(mark.get("folder") or "")
        row["pinned"] = bool(mark.get("pinned"))
        row["pinned_at"] = float(mark.get("pinned") or 0)
    rows.sort(key=lambda r: (0 if r.get("pinned") else 1,
                             -float(r.get("pinned_at") or 0),
                             -float(r.get("at") or 0)))
    return rows


def _write_item(data: dict, entry_id: str, item: dict) -> None:
    if item:
        data.setdefault("items", {})[entry_id] = item
    else:
        data.get("items", {}).pop(entry_id, None)
    save(data)
