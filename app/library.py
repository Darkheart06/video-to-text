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
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import i18n, merge, presets, render

# Скрытый список рядом с записями: он только ускоряет открытие папки, и его
# можно спокойно удалить.
INDEX_NAME = ".library.json"
RESULT_SUFFIX = ".result.json"

# Какой файл к какому месту в карточке относится.
SUFFIXES = [
    (".summary.md", "summary"),
    *[(name, "tables") for name in i18n.table_suffixes()],
    (".transcript.md", "transcript_md"),
    (".transcript.txt", "transcript_txt"),
    (".subtitles.srt", "subtitles"),
    (".result.json", "result"),
    (".wav", "audio"),
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
        return out
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
    return _filter(found, query)


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
        if candidate.exists():
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
    """Убирает запись целиком — со всеми файлами, в Корзину, а не насовсем."""
    path = _path_of(where, entry_id)
    if path is None:
        return {"ok": False, "error": i18n.t("lib.missing")}
    stem = path.name[: -len(RESULT_SUFFIX)]
    targets = list(files_of(path.parent, stem).values())
    if not targets:
        return {"ok": False, "error": i18n.t("lib.gone")}

    removed = _to_trash(targets)
    for target in targets:
        _cache.pop(target, None)
        _text_cache.pop(target, None)
    index = _load_index(path.parent)
    index.pop(str(path), None)
    _save_index(path.parent, index)
    return {"ok": True, "removed": removed, "title": stem}


def _to_trash(targets: list[str]) -> int:
    """На маке отправляем в Корзину: удалить безвозвратно чужие файлы — плохая
    идея, человек может передумать."""
    if sys.platform == "darwin":
        items = ", ".join(f'POSIX file "{t}"' for t in targets)
        script = f'tell application "Finder" to delete {{{items}}}'
        try:
            done = subprocess.run(["osascript", "-e", script],
                                  capture_output=True, timeout=30)
            if done.returncode == 0:
                return len(targets)
        except Exception:
            pass
    removed = 0
    for target in targets:
        try:
            Path(target).unlink()
            removed += 1
        except OSError:
            pass
    return removed


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
