"""Документы, приложенные к записи.

Созвон почти никогда не самодостаточен: обсуждают смету, техзадание, письмо
заказчика — и в расшифровке остаётся «как в том документе». Через неделю по
задаче «доработать по замечаниям» уже не понять, о каких замечаниях речь: сам
документ живёт отдельно, в почте или на диске.

Поэтому документы кладутся рядом с записью, в папку `<запись>.files`, и их
текст уходит в модель вместе с расшифровкой. Файлы копируются, а не
запоминаются ссылкой: исходник переименуют или удалят, а запись должна
оставаться цельной и через год.

Текст достаём тем, что есть в системе, без тяжёлых зависимостей: docx и pptx —
это zip с xml, pdf читает `pdftotext` из ffmpeg-соседей или, если его нет,
встроенный разбор `pypdf`. Не получилось — файл всё равно лежит рядом и
открывается кликом, просто модель его не увидит.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from . import i18n

RESULT_SUFFIX = ".result.json"
FOLDER_SUFFIX = ".files"

# Что имеет смысл читать. Картинки и архивы лежат рядом просто как файлы.
TEXT_KINDS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rtf"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".tiff", ".bmp"}
DOC_KINDS = {".docx", ".pptx", ".xlsx"}
PDF_KINDS = {".pdf"}

# Сколько текста берём в модель. Расшифровка часового созвона — тысяч сорок
# знаков; документы не должны её вытеснять, поэтому им отводится доля.
TEXT_LIMIT = 12000
PER_FILE_LIMIT = 6000


def folder_for(result_path: str | Path, create: bool = False) -> Path:
    path = Path(result_path)
    stem = path.name[: -len(RESULT_SUFFIX)] if path.name.endswith(RESULT_SUFFIX) \
        else path.stem
    box = path.parent / f"{stem}{FOLDER_SUFFIX}"
    if create:
        box.mkdir(parents=True, exist_ok=True)
    return box


def add(result_path: str | Path, files: list[str]) -> dict:
    """Кладёт документы рядом с записью. Возвращает, что легло."""
    box = folder_for(result_path, create=True)
    added = []
    for item in files or []:
        source = Path(str(item)).expanduser()
        if not source.exists() or source.is_dir():
            continue
        target = box / source.name
        # Файл с таким именем уже приложен — не затираем: у второй версии
        # договора то же имя, а разница важна.
        if target.exists():
            for n in range(2, 50):
                candidate = box / f"{source.stem} ({n}){source.suffix}"
                if not candidate.exists():
                    target = candidate
                    break
        try:
            shutil.copy2(source, target)
            added.append(target.name)
        except OSError:
            continue
    return {"ok": bool(added), "added": added}


def items(result_path: str | Path) -> list[dict]:
    """Что приложено к записи — для карточки в окне."""
    box = folder_for(result_path)
    if not box.exists():
        return []
    found = []
    for file in sorted(box.iterdir()):
        if file.name.startswith(".") or file.is_dir():
            continue
        image = file.suffix.lower() in IMAGE_KINDS
        found.append({
            "name": file.name,
            "path": str(file),
            "size": file.stat().st_size,
            # Картинку показываем превью, документ — строкой: смешивать их в
            # один список неудобно, глазу нужны разные полки.
            "kind": "image" if image else "doc",
            "readable": False if image else bool(_extract(file, limit=200).strip()),
        })
    return found


def remove(result_path: str | Path, name: str) -> dict:
    box = folder_for(result_path)
    target = box / Path(str(name or "")).name
    if not target.exists() or target.parent != box:
        return {"ok": False, "error": i18n.t("attach.missing")}
    try:
        target.unlink()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if not any(box.iterdir()):
        box.rmdir()
    return {"ok": True}


def context(result_path: str | Path, limit: int = TEXT_LIMIT) -> str:
    """Текст приложенных документов — тот, что уходит в модель.

    Каждый документ подписан именем: модели важно знать, что «в смете» и «в
    письме» — разные источники, иначе она смешивает их в один голос.
    """
    parts, used = [], 0
    for item in items(result_path):
        text = _extract(Path(item["path"]), limit=PER_FILE_LIMIT).strip()
        if not text:
            continue
        block = f"### {item['name']}\n{text}"
        if used + len(block) > limit:
            block = block[: max(0, limit - used)]
        if not block.strip():
            break
        parts.append(block)
        used += len(block)
        if used >= limit:
            break
    return "\n\n".join(parts)


def names(result_path: str | Path) -> list[str]:
    return [item["name"] for item in items(result_path)]


# --- превью картинок ---------------------------------------------------------

# Готовые превью держим в памяти: окно перерисовывается часто, а картинка та же.
_thumbs: dict[tuple[str, float, int], str] = {}


def preview(path: str | Path, size: int = 256) -> str:
    """Картинка как data-URI, уменьшенная до квадрата size×size.

    Уменьшаем средствами системы (`sips` есть на любом маке), а если их нет —
    отдаём исходник, но только когда он маленький: гнать пятимегабайтное фото
    в окно ради превью незачем.
    """
    import base64
    import mimetypes
    import subprocess
    import tempfile

    file = Path(path)
    if not file.exists() or file.suffix.lower() not in IMAGE_KINDS:
        return ""
    key = (str(file), file.stat().st_mtime, size)
    if key in _thumbs:
        return _thumbs[key]

    data, kind = b"", mimetypes.guess_type(file.name)[0] or "image/png"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / "thumb.png"
            done = subprocess.run(
                ["sips", "-Z", str(size), "--out", str(small), str(file)],
                capture_output=True, timeout=30)
            if done.returncode == 0 and small.exists():
                data, kind = small.read_bytes(), "image/png"
    except Exception:
        data = b""
    if not data:
        try:
            from PIL import Image

            with tempfile.TemporaryDirectory() as tmp:
                small = Path(tmp) / "thumb.png"
                image = Image.open(file)
                image.thumbnail((size, size))
                image.convert("RGB").save(small, "PNG")
                data, kind = small.read_bytes(), "image/png"
        except Exception:
            data = b""
    if not data and file.stat().st_size <= 2_000_000:
        data = file.read_bytes()
    if not data:
        return ""
    uri = f"data:{kind};base64," + base64.b64encode(data).decode()
    if len(_thumbs) > 60:
        _thumbs.clear()
    _thumbs[key] = uri
    return uri


# --- извлечение текста -------------------------------------------------------

def _extract(file: Path, limit: int = PER_FILE_LIMIT) -> str:
    suffix = file.suffix.lower()
    try:
        if suffix in TEXT_KINDS:
            text = file.read_text("utf-8", errors="replace")
            return _strip_rtf(text) if suffix == ".rtf" else text[:limit]
        if suffix in DOC_KINDS:
            return _from_office(file)[:limit]
        if suffix in PDF_KINDS:
            return _from_pdf(file)[:limit]
    except Exception:
        return ""
    return ""


def _from_office(file: Path) -> str:
    """docx, pptx и xlsx — это zip с xml: текст лежит между тегами."""
    wanted = {
        ".docx": ("word/document.xml",),
        ".pptx": None,             # слайдов много, имена заранее не известны
        ".xlsx": ("xl/sharedStrings.xml",),
    }[file.suffix.lower()]
    chunks = []
    with zipfile.ZipFile(file) as zf:
        members = (wanted if wanted is not None
                   else [n for n in zf.namelist()
                         if n.startswith("ppt/slides/slide") and n.endswith(".xml")])
        for name in members:
            try:
                raw = zf.read(name).decode("utf-8", "replace")
            except KeyError:
                continue
            # Абзацы и ячейки разделяем переводом строки, иначе весь документ
            # слипается в одну строку и модель теряет структуру.
            raw = re.sub(r"</(w:p|a:p|si|w:tr)>", "\n", raw)
            chunks.append(re.sub(r"<[^>]+>", "", raw))
    return _tidy("\n".join(chunks))


def _from_pdf(file: Path) -> str:
    """Сначала пробуем системный pdftotext, потом pypdf, если он поставлен."""
    import subprocess

    for binary in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext",
                   "/usr/bin/pdftotext", "pdftotext"):
        try:
            done = subprocess.run([binary, "-layout", str(file), "-"],
                                  capture_output=True, timeout=60)
            if done.returncode == 0 and done.stdout.strip():
                return _tidy(done.stdout.decode("utf-8", "replace"))
        except Exception:
            continue
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(file))
        return _tidy("\n".join((page.extract_text() or "") for page in reader.pages))
    except Exception:
        return ""


def _strip_rtf(text: str) -> str:
    text = re.sub(r"\\\\'([0-9a-fA-F]{2})", " ", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", text)
    return _tidy(text.replace("{", " ").replace("}", " "))


def _tidy(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
