"""Архив расшифровок: всё, что уже разобрано, — списком в самом окне.

Отдельной базы нет и не нужно: каждая запись и так оставляет после себя
``.result.json``, где лежит и транскрипт, и саммари, и кто сколько говорил.
Модуль собирает по этим файлам список, помнит его в ``.записи.json`` рядом с
ними и по клику разворачивает любую запись обратно в тот же вид, что и сразу
после обработки.

Такой подход переживает и переустановку приложения, и ручное копирование папки:
пока файлы на месте, архив собирается заново сам.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import i18n, media, merge, presets, render, shelf

# Скрытый список рядом с записями: он только ускоряет открытие папки, и его
# можно спокойно удалить.
INDEX_NAME = ".library.json"
RESULT_SUFFIX = ".result.json"

# Своя корзина рядом с записями. Раньше файлы уходили в системную Корзину, и
# вернуть их можно было только через Finder — вслепую, по именам файлов. Здесь
# запись лежит целиком, со своим описанием, и возвращается на место одной
# кнопкой. Точка в начале имени прячет папку от глаз и от поиска по архиву.
TRASH_NAME = ".trash"
TRASH_META = "meta.json"
TRASH_DAYS = 30

# Какой файл к какому месту в карточке относится.
SUFFIXES = [
    (".summary.md", "summary"),
    *[(name, "tables") for name in i18n.table_suffixes()],
    (".transcript.md", "transcript_md"),
    (".transcript.txt", "transcript_txt"),
    (".subtitles.srt", "subtitles"),
    (".chapters.vtt", "chapters"),
    (".result.json", "result"),
    (".wav", "audio"),
    # Запись экрана лежит рядом со звуком: плеер в окне играет её, а метки
    # ведут к нужной минуте.
    (".mp4", "video"),
]

# Разобранные файлы держим в памяти: список обновляется часто, а на диске
# ничего не меняется — перечитывать json каждый раз незачем.
_cache: dict[str, tuple[float, dict]] = {}
_text_cache: dict[str, tuple[float, str]] = {}


@dataclass
class Entry:
    id: str
    path: str
    title: str
    kind: str          # "call" — записанный созвон, "file" — разобранный файл
    at: float          # когда появилось, unix-время
    when: str          # человеческая дата для списка
    duration: float
    language: str
    speakers: int
    lines: int
    preset: str
    preview: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# --- список -----------------------------------------------------------------

def entries(where, query: str = "", lang: str = "") -> list[dict]:
    """Все разобранные записи, новые сверху. Пустой запрос — весь список.

    Папок может быть несколько: у английского и русского языка имена разные, а
    архив должен показывать всё, что человек уже разобрал.
    """
    folders = _folders(where)
    if not folders:
        return []
    if len(folders) > 1:
        out: list[dict] = []
        for folder in folders:
            out += entries(folder, query, lang)
        out.sort(key=lambda e: -float(e.get("at") or 0))
        return shelf.decorate(out)
    output_dir = folders[0]

    index = _load_index(output_dir)
    found: list[dict] = []
    seen: set[str] = set()

    for path in sorted(output_dir.glob(f"*{RESULT_SUFFIX}")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        key = str(path)
        seen.add(key)
        entry = _cached_entry(key, mtime, index)
        if entry is None:
            entry = _read_entry(path, mtime, lang)
            if entry is None:
                continue
            _cache[key] = (mtime, entry)
            index[key] = {"mtime": mtime, "entry": entry}
        found.append(entry)

    # Файлы могли удалить руками — чистим и память, и сохранённый список.
    for gone in [k for k in index if k not in seen]:
        index.pop(gone, None)
        _cache.pop(gone, None)
        _text_cache.pop(gone, None)
    _save_index(output_dir, index)

    found.sort(key=lambda e: e["at"], reverse=True)
    return shelf.decorate(_filter(found, query))


def _cached_entry(key: str, mtime: float, index: dict) -> dict | None:
    hit = _cache.get(key)
    if hit and abs(hit[0] - mtime) < 0.001:
        return hit[1]
    saved = index.get(key)
    if saved and abs(float(saved.get("mtime", 0)) - mtime) < 0.001:
        entry = saved.get("entry")
        if entry:
            _cache[key] = (mtime, entry)
            return entry
    return None


def _filter(found: list[dict], query: str) -> list[dict]:
    words = [w for w in re.split(r"\s+", (query or "").strip().lower()) if w]
    if not words:
        return found
    out = []
    for entry in found:
        haystack = f"{entry['title']} {entry['when']} {entry['preview']}".lower()
        if all(w in haystack for w in words):
            out.append(entry)
            continue
        # По коротким словам искать внутри расшифровок не стоит: «а» найдётся
        # везде. С трёх букв — уже осмысленно.
        if any(len(w) >= 3 for w in words) and all(
                w in haystack or w in _transcript_text(entry["path"]) for w in words):
            out.append(dict(entry, matched=i18n.t("lib.in_text")))
    return out


def _transcript_text(result_path: str) -> str:
    path = Path(result_path[: -len(RESULT_SUFFIX)] + ".transcript.txt")
    if not path.exists():
        return ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    hit = _text_cache.get(str(path))
    if hit and abs(hit[0] - mtime) < 0.001:
        return hit[1]
    try:
        text = path.read_text("utf-8").lower()
    except OSError:
        text = ""
    _text_cache[str(path)] = (mtime, text)
    return text


# --- разбор одного файла ----------------------------------------------------

def _read_entry(path: Path, mtime: float, lang: str = "") -> dict | None:
    data = _load_json(path)
    if not data:
        return None
    meta = data.get("meta") or {}
    summary = data.get("summary") or {}
    sections = summary.get("sections") or {}
    turns = data.get("turns") or []
    stem = path.name[: -len(RESULT_SUFFIX)]

    preview = _preview(sections, turns)
    models = str(meta.get("models", ""))
    return Entry(
        id=_ident(path),
        path=str(path),
        title=str(meta.get("title") or stem),
        kind="call" if _is_call(models) else "file",
        at=mtime,
        when=_when(meta.get("processed_at"), mtime),
        duration=float(meta.get("duration") or 0),
        language=str(meta.get("language") or ""),
        speakers=len(data.get("speakers")
                     or _speakers_from_turns(turns, models, lang)),
        lines=len(turns),
        preset=str(summary.get("preset") or ""),
        preview=preview,
    ).as_dict()


def _preview(sections: dict, turns: list) -> str:
    """Одна строка для списка: первая мысль из саммари, иначе начало разговора."""
    for value in sections.values():
        text = re.sub(r"[|*_`#>\-]+", " ", str(value))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 12:
            return text[:160]
    for turn in turns[:3]:
        text = str(turn.get("text", "")).strip()
        if len(text) > 12:
            return text[:160]
    return ""


def _when(processed_at, mtime: float) -> str:
    if processed_at:
        return str(processed_at)
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def _ident(path: Path) -> str:
    return "lib" + hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


# --- открыть запись ---------------------------------------------------------

def snapshot(where, entry_id: str, lang: str = "") -> dict | None:
    """Разворачивает запись обратно в карточку — ту же, что после обработки."""
    path = _path_of(where, entry_id)
    if path is None:
        return None
    data = _load_json(path)
    if not data:
        return None

    meta = data.get("meta") or {}
    summary = data.get("summary") or {}
    sections = summary.get("sections") or {}
    turns = data.get("turns") or []
    speakers = {
        key: {"label": value.get("label", key),
              "seconds": float(value.get("speaking_seconds") or 0)}
        for key, value in (data.get("speakers") or {}).items()
    }
    if not speakers:
        speakers = _speakers_from_turns(turns, str(meta.get("models", "")), lang)
    stem = path.name[: -len(RESULT_SUFFIX)]

    return {
        "id": _ident(path),
        "source": str(meta.get("source") or path),
        "title": str(meta.get("title") or stem),
        "status": "done",
        "stage": "done",
        "message": i18n.t("lib.from_archive"),
        "progress": 1.0,
        "error": "",
        "files": files_of(path.parent, stem),
        "meta": meta,
        "summary_md": summary.get("markdown", ""),
        "summary_sections": sections,
        "summary_tabs": [list(x) for x in _tabs(summary, sections, lang)],
        "preset": summary.get("preset", ""),
        "transcript_md": "",
        "speakers": speakers,
        "marks": data.get("marks") or [],
        # Договорённость о следующем созвоне, найденная при разборе: карточка
        # предлагает завести её в расписание.
        "next_call": (data.get("meta") or {}).get("next_call") or None,
        "warnings": [],
        "archived": True,
        "turns": turns,
    }


def _is_call(models: str) -> bool:
    """Записанный созвон видно по строке моделей: там сказано, чем писали."""
    low = (models or "").lower()
    return any(word in low for word in ("созвон", "запис", "call", "recording", "meeting"))


def _speakers_from_turns(turns: list, models: str, lang: str = "") -> dict:
    """Записи, сделанные до того, как имена стали попадать в result.json.

    Без этого созвон из архива открывался бы с подписями «S1» и «S2».
    """
    seconds: dict[str, float] = {}
    for turn in turns:
        key = str(turn.get("speaker") or "")
        if not key or key == "unknown":
            continue
        seconds[key] = seconds.get(key, 0.0) + max(
            0.0, float(turn.get("end") or 0) - float(turn.get("start") or 0))
    call = _is_call(models) and set(seconds) <= {"S1", "S2"}
    fallback = {"S1": i18n.d("me", lang), "S2": i18n.d("them", lang)}
    return {
        key: {"label": fallback[key] if call else i18n.d("speaker", lang, n=key[1:]),
              "seconds": round(sec, 1)}
        for key, sec in sorted(seconds.items())
    }


def _tabs(summary: dict, sections: dict, lang: str = "") -> list[tuple[str, str]]:
    saved = summary.get("tabs")
    if saved:
        return [(str(k), str(v)) for k, v in saved if k and v]
    return presets.tabs_for(list(sections), lang)


def files_of(directory: Path, stem: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for suffix, key in SUFFIXES:
        candidate = directory / f"{stem}{suffix}"
        if not candidate.exists():
            continue
        # Недописанная запись экрана в список не попадает: плеер на ней всё
        # равно покажет чёрный прямоугольник, а в файлах она выглядит как
        # обещание, которого никто не выполнит.
        if key == "video" and not media.playable_mp4(candidate):
            continue
        out[key] = str(candidate)
    return out


def _folders(where) -> list[Path]:
    items = where if isinstance(where, (list, tuple, set)) else [where]
    return [Path(x) for x in items if Path(x).exists()]


def _path_of(where, entry_id: str) -> Path | None:
    for folder in _folders(where):
        for path in folder.glob(f"*{RESULT_SUFFIX}"):
            if _ident(path) == entry_id:
                return path
    return None


# --- переименование спикеров ------------------------------------------------

def rename(where, entry_id: str, names: dict[str, str], lang: str = "") -> dict | None:
    """Меняет подписи спикеров у записи из архива и переписывает её файлы.

    Имена людей вспоминаются обычно потом, когда запись уже давно разобрана, —
    поэтому переименование работает и здесь, а не только сразу после обработки.
    """
    path = _path_of(where, entry_id)
    if path is None:
        return None
    data = _load_json(path)
    if not data:
        return None

    names = {k: v.strip() for k, v in (names or {}).items() if v and v.strip()}
    speakers = data.get("speakers") or {}
    for key, value in speakers.items():
        value["label"] = names.get(key, value.get("label", key))
    # Двум голосам дали одно имя — значит, это один человек, и держать их
    # порознь незачем: в карточке он двоился бы, а голосовой отпечаток
    # запоминался бы дважды по половине реплик.
    _fold_same_names(data)
    speakers = data.get("speakers") or {}
    labels = {key: value.get("label", key) for key, value in speakers.items()}

    turns = _turns_of(data)
    meta = data.get("meta") or {}
    stem = path.name[: -len(RESULT_SUFFIX)]
    directory = path.parent
    (directory / f"{stem}.transcript.md").write_text(
        render.transcript_markdown(turns, meta, labels, lang), "utf-8")
    (directory / f"{stem}.transcript.txt").write_text(
        render.plain_transcript(turns, labels, lang), "utf-8")
    subtitles = directory / f"{stem}.subtitles.srt"
    if subtitles.exists():
        subtitles.write_text(render.srt(turns, labels, lang=lang), "utf-8")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    _cache.pop(str(path), None)
    _text_cache.pop(str(directory / f"{stem}.transcript.txt"), None)
    return snapshot(directory, entry_id, lang)


def reassign(where, entry_id: str, index: int, name: str, lang: str = "") -> dict | None:
    """Отдаёт одну реплику другому человеку.

    Разделение по голосам ошибается, и до сих пор поправить его можно было
    только целиком — переименовав весь голос. А ошибка обычно точечная: две-три
    реплики уехали к соседу. Это важно не только для чтения: по этим же
    репликам запоминаются голосовые отпечатки, и запомнить чужой кусок под
    своим именем хуже, чем не запоминать вовсе.

    Имя может быть и новым — тогда заводится ещё один голос.
    """
    path = _path_of(where, entry_id)
    if path is None:
        return None
    data = _load_json(path)
    if not data:
        return None
    turns = data.get("turns") or []
    if not (0 <= int(index) < len(turns)):
        return None
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
    if not name:
        return None

    speakers = data.setdefault("speakers", {})
    key = next((k for k, v in speakers.items()
                if str(v.get("label") or k) == name), None)
    if key is None:
        key = _free_key(speakers)
        speakers[key] = {"label": name, "speaking_seconds": 0.0}
    turns[int(index)]["speaker"] = key

    _recount(data)
    _fold_same_names(data)
    _rewrite(path, data, lang)
    return snapshot(path.parent, entry_id, lang)


def _free_key(speakers: dict) -> str:
    for n in range(1, 200):
        key = f"S{n}"
        if key not in speakers:
            return key
    return "S999"


def _recount(data: dict) -> None:
    """Пересчитывает, кто сколько говорил, и убирает опустевшие голоса."""
    spoken: dict[str, float] = {}
    for turn in data.get("turns") or []:
        key = str(turn.get("speaker") or "")
        if not key:
            continue
        spoken[key] = spoken.get(key, 0.0) + max(
            0.0, float(turn.get("end") or 0) - float(turn.get("start") or 0))
    speakers = data.get("speakers") or {}
    for key in list(speakers):
        if key in spoken:
            speakers[key]["speaking_seconds"] = round(spoken[key], 1)
        else:
            speakers.pop(key)


def _fold_same_names(data: dict) -> None:
    """Сводит голоса с одинаковой подписью в один."""
    speakers = data.get("speakers") or {}
    first: dict[str, str] = {}
    moved: dict[str, str] = {}
    for key in list(speakers):
        label = str(speakers[key].get("label") or key)
        if label in first:
            moved[key] = first[label]
            speakers.pop(key)
        else:
            first[label] = key
    if not moved:
        return
    for turn in data.get("turns") or []:
        key = str(turn.get("speaker") or "")
        if key in moved:
            turn["speaker"] = moved[key]
    _recount(data)


def _rewrite(path: Path, data: dict, lang: str = "") -> None:
    """Переписывает файлы записи после правки разметки."""
    speakers = data.get("speakers") or {}
    labels = {key: str(value.get("label") or key) for key, value in speakers.items()}
    turns = _turns_of(data)
    meta = data.get("meta") or {}
    stem = path.name[: -len(RESULT_SUFFIX)]
    directory = path.parent
    (directory / f"{stem}.transcript.md").write_text(
        render.transcript_markdown(turns, meta, labels, lang), "utf-8")
    (directory / f"{stem}.transcript.txt").write_text(
        render.plain_transcript(turns, labels, lang), "utf-8")
    subtitles = directory / f"{stem}.subtitles.srt"
    if subtitles.exists():
        subtitles.write_text(render.srt(turns, labels, lang=lang), "utf-8")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    _cache.pop(str(path), None)
    _text_cache.pop(str(directory / f"{stem}.transcript.txt"), None)


def retitle(where, entry_id: str, title: str, lang: str = "") -> dict:
    """Меняет название записи — и в документах, и в именах файлов.

    Название приложение придумывает само, по теме разговора, и попадает не
    всегда. Исправлять его нужно там же, где читают, — поэтому правка идёт не
    только в `.result.json`, но и по именам файлов: иначе в Finder осталась бы
    старая подпись, и одна и та же запись называлась бы двумя способами.
    """
    path = _path_of(where, entry_id)
    if path is None:
        return {"ok": False, "error": i18n.t("lib.missing")}
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:120]
    if not title:
        return {"ok": False, "error": i18n.t("lib.emptyTitle")}

    data = _load_json(path)
    if data is None:
        return {"ok": False, "error": i18n.t("lib.missing")}
    directory = path.parent
    stem = path.name[: -len(RESULT_SUFFIX)]
    fresh = render.safe_stem(title)
    if fresh != stem:
        fresh = _free_stem(directory, fresh)

    moved: dict[str, str] = {}
    for name, target in files_of(directory, stem).items():
        source = Path(target)
        suffix = source.name[len(stem):]
        destination = directory / f"{fresh}{suffix}"
        if destination == source:
            moved[name] = str(source)
            continue
        try:
            source.replace(destination)
            moved[name] = str(destination)
        except OSError:
            moved[name] = str(source)
    # Прикреплённые документы лежат в папке рядом и должны переехать следом,
    # иначе запись потеряет их молча.
    papers = directory / f"{stem}.files"
    if papers.is_dir() and fresh != stem:
        try:
            papers.replace(directory / f"{fresh}.files")
        except OSError:
            pass

    result = Path(moved.get("result") or (directory / f"{fresh}{RESULT_SUFFIX}"))
    meta = data.setdefault("meta", {})
    meta["title"] = title
    if moved.get("audio"):
        meta["source"] = moved["audio"]
    meta["files"] = dict(moved)
    result.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    forget_cache(path)
    forget_cache(result)
    shelf.move(entry_id, _ident(result))
    return {"ok": True, "id": _ident(result), "title": title}


def _free_stem(directory: Path, stem: str) -> str:
    """Чужую запись не затираем, даже если названия совпали."""
    if not (directory / f"{stem}{RESULT_SUFFIX}").exists():
        return stem
    for n in range(2, 60):
        candidate = f"{stem} ({n})"
        if not (directory / f"{candidate}{RESULT_SUFFIX}").exists():
            return candidate
    return stem


def forget_cache(path: Path) -> None:
    """Файлы записи переписали — забываем всё, что о ней помнили."""
    stem = path.name[: -len(RESULT_SUFFIX)]
    for name in list(_cache) + list(_text_cache):
        if stem in name:
            _cache.pop(name, None)
            _text_cache.pop(name, None)
    index = _load_index(path.parent)
    index.pop(str(path), None)
    _save_index(path.parent, index)


def _turns_of(data: dict) -> list[merge.Turn]:
    """Собирает реплики обратно в объекты, которые понимает ``render``."""
    out = []
    for item in data.get("turns") or []:
        key = str(item.get("speaker") or "")
        number = None
        if key.startswith("S") and key[1:].isdigit():
            number = int(key[1:]) - 1
        out.append(merge.Turn(
            start=float(item.get("start") or 0), end=float(item.get("end") or 0),
            speaker=number, text=str(item.get("text") or ""),
            raw=str(item.get("raw") or ""),
        ))
    return out


# --- удаление ---------------------------------------------------------------

def delete(where, entry_id: str) -> dict:
    """Убирает запись в корзину приложения — целиком и с возможностью вернуть."""
    path = _path_of(where, entry_id)
    if path is None:
        return {"ok": False, "error": i18n.t("lib.missing")}
    stem = path.name[: -len(RESULT_SUFFIX)]
    targets = list(files_of(path.parent, stem).values())
    if not targets:
        return {"ok": False, "error": i18n.t("lib.gone")}

    entry = _read_entry(path, path.stat().st_mtime)
    box = _trash_dir(path.parent) / f"{int(time.time())}-{entry_id}"
    box.mkdir(parents=True, exist_ok=True)
    moved = []
    for target in targets:
        source = Path(target)
        try:
            source.replace(box / source.name)
            moved.append(source.name)
        except OSError:
            continue
    (box / TRASH_META).write_text(json.dumps({
        "id": box.name,
        "title": (entry or {}).get("title") or stem,
        "stem": stem,
        "home": str(path.parent),
        "deleted_at": time.time(),
        "files": moved,
    }, ensure_ascii=False), "utf-8")

    for target in targets:
        _cache.pop(target, None)
        _text_cache.pop(target, None)
    index = _load_index(path.parent)
    index.pop(str(path), None)
    _save_index(path.parent, index)
    return {"ok": True, "removed": len(moved), "title": (entry or {}).get("title") or stem}


# --- корзина ----------------------------------------------------------------

def _trash_dir(folder: Path) -> Path:
    return Path(folder) / TRASH_NAME


def _boxes(where) -> list[Path]:
    found = []
    for folder in _folders(where):
        trash = _trash_dir(folder)
        if trash.exists():
            found += [box for box in trash.iterdir() if (box / TRASH_META).exists()]
    return found


def trash(where, days: int = TRASH_DAYS, lang: str = "") -> list[dict]:
    """Что лежит в корзине. Заодно выметает то, чей срок вышел."""
    sweep(where, days)
    lang = i18n.pick(lang, i18n.current())
    items = []
    for box in _boxes(where):
        meta = _load_json(box / TRASH_META) or {}
        # Считаем прожитые сутки, а не доли: запись, удалённая минуту назад,
        # должна показывать полный срок, а не «осталось 29».
        lived = int((time.time() - float(meta.get("deleted_at") or 0)) // 86400)
        left = max(0, days - lived)
        items.append({
            "id": box.name,
            "title": meta.get("title") or box.name,
            "when": _when("", float(meta.get("deleted_at") or 0)),
            "days_left": left,
            "files": len(meta.get("files") or []),
        })
    items.sort(key=lambda item: item["id"], reverse=True)
    return items


def restore(where, trash_id: str) -> dict:
    """Возвращает запись туда, откуда её удалили."""
    for box in _boxes(where):
        if box.name != trash_id:
            continue
        meta = _load_json(box / TRASH_META) or {}
        home = Path(meta.get("home") or "")
        if not home.exists():
            # Папку могли переименовать или перенести — кладём к остальным.
            folders = _folders(where)
            if not folders:
                return {"ok": False, "error": i18n.t("lib.missing")}
            home = folders[0]
        back = 0
        for item in box.iterdir():
            if item.name == TRASH_META:
                continue
            target = home / item.name
            # Запись с таким именем могли завести заново — не затираем её.
            if target.exists():
                target = home / f"{target.stem} (2){target.suffix}"
            try:
                item.replace(target)
                back += 1
            except OSError:
                continue
        _drop(box)
        return {"ok": True, "restored": back, "title": meta.get("title") or trash_id}
    return {"ok": False, "error": i18n.t("lib.missing")}


def purge(where, trash_id: str = "") -> dict:
    """Удаляет из корзины навсегда — одну запись или всё."""
    gone = 0
    for box in _boxes(where):
        if trash_id and box.name != trash_id:
            continue
        _drop(box)
        gone += 1
    return {"ok": True, "removed": gone}


def sweep(where, days: int = TRASH_DAYS) -> int:
    """Выметает записи, пролежавшие в корзине дольше срока."""
    if days <= 0:
        return 0
    limit = time.time() - days * 86400
    gone = 0
    for box in _boxes(where):
        meta = _load_json(box / TRASH_META) or {}
        if float(meta.get("deleted_at") or 0) < limit:
            _drop(box)
            gone += 1
    return gone


def _drop(box: Path) -> None:
    for item in box.iterdir():
        try:
            item.unlink()
        except OSError:
            pass
    try:
        box.rmdir()
    except OSError:
        pass


# --- сохранённый список -----------------------------------------------------

def _index_path(output_dir: Path) -> Path:
    return Path(output_dir) / INDEX_NAME


def _load_index(output_dir: Path) -> dict:
    path = _index_path(output_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def _save_index(output_dir: Path, index: dict) -> None:
    try:
        _index_path(output_dir).write_text(
            json.dumps({"version": 1, "items": index}, ensure_ascii=False), "utf-8")
    except OSError:
        pass
