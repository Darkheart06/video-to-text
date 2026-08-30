"""Метки на записи: где прозвучало то, что попало в документ.

Расшифровка на час — это стена текста, и найти в ней момент, когда назвали
сумму или договорились о сроке, тяжелее, чем переслушать. Зато у нас уже есть
две вещи: саммари с решениями и задачами и реплики с таймкодами. Значит, можно
поставить метки — как главы у видео: нажал и попал в нужную минуту.

**Время берём из расшифровки, а не у модели.** Спросить модель «во сколько это
сказали» — верный способ получить правдоподобное выдуманное число. Поэтому
каждый пункт документа сопоставляется с репликами по словам, и метка получает
время найденной реплики. Не нашлось убедительного совпадения — метки нет: пустое
место честнее, чем метка, ведущая не туда.
"""

from __future__ import annotations

import re

from . import i18n

# Насколько пункт должен совпасть с репликой, чтобы метке можно было верить.
# Ниже — почти всегда случайные общие слова («мы», «нужно», «сделать»).
FLOOR = 0.34

# Слова короче трёх букв и служебные не значат ничего: по ним совпадает всё.
STOP = {
    "это", "как", "что", "для", "при", "или", "все", "уже", "они", "нам", "там",
    "так", "его", "нет", "над", "под", "мы", "по", "на", "не", "из", "до", "то",
    "the", "and", "for", "that", "with", "this", "from", "have", "will", "are",
}

# Какие разделы дают метки. Смысл имеет то, к чему возвращаются: решения,
# задачи, риски. Пересказ саммари метить незачем — он про всю запись сразу.
KINDS = ("decisions", "tasks", "risks", "open", "works")


def build(turns: list, sections: dict, notes: list | None = None,
          lang: str = "") -> list[dict]:
    """Метки для шкалы: время, вид и короткая подпись."""
    lang = i18n.pick(lang, i18n.current())
    spoken = [(float(getattr(t, "start", 0) or 0), _words(getattr(t, "text", "") or ""))
              for t in (turns or [])]
    marks: list[dict] = []

    for note in notes or []:
        at = float(note.get("at") or 0)
        for line in _lines(str(note.get("text") or "")):
            marks.append({"at": at, "kind": "note", "text": line})
            break        # одной строки на заметку достаточно: она и так короткая

    for kind in KINDS:
        for line in _lines(str((sections or {}).get(kind) or "")):
            at, score = _when(line, spoken)
            if at is None:
                continue
            marks.append({"at": at, "kind": kind, "text": line, "score": round(score, 2)})

    marks.sort(key=lambda m: m["at"])
    return _thin(marks)


def to_vtt(marks: list[dict], lang: str = "") -> str:
    """Главы в формате WebVTT — их понимают плееры и YouTube."""
    lang = i18n.pick(lang, i18n.current())
    out = ["WEBVTT", ""]
    for i, mark in enumerate(marks, 1):
        end = marks[i]["at"] if i < len(marks) else mark["at"] + 30
        out += [str(i), f"{_stamp(mark['at'])} --> {_stamp(max(end, mark['at'] + 1))}",
                f"{i18n.d('mark.' + mark['kind'], lang)}: {mark['text']}", ""]
    return "\n".join(out)


# --- внутреннее --------------------------------------------------------------

def _lines(markdown: str) -> list[str]:
    """Пункты списка и строки таблицы — без разметки и без заголовков."""
    found = []
    for raw in markdown.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or set("".join(cells)) <= set("-: "):
                continue          # разделитель таблицы
            line = cells[0]
            if line.lower().startswith(("задача", "работа", "task", "job", "**итого")):
                continue          # шапка и строка итога
        line = re.sub(r"^[-*•]\s*", "", line)
        line = re.sub(r"[*_`]", "", line).strip()
        if len(line) >= 8:
            found.append(line[:120])
    return found


# Сколько букв слова считаем корнем. Документ и расшифровка почти никогда не
# совпадают дословно: в решении «обсуждение остановлено», а в реплике —
# «остановить». По целым словам это разные слова, и метка не ставилась там, где
# человек её ждёт. Пять букв — та длина, на которой русские формы одного слова
# сходятся, а разные слова ещё расходятся.
STEM = 5


def _words(text: str) -> set[str]:
    """Корни значимых слов: по ним и сравниваем пункт с репликой."""
    out = set()
    for word in re.findall(r"[\w-]{3,}", (text or "").lower()):
        if word in STOP:
            continue
        out.add(word[:STEM])
    return out


def _when(line: str, spoken: list[tuple[float, set[str]]]) -> tuple[float | None, float]:
    """Когда это прозвучало. Ищем реплику, где слов пункта больше всего."""
    wanted = _words(line)
    if len(wanted) < 2 or not spoken:
        return None, 0.0
    best_at, best = None, 0.0
    for at, said in spoken:
        if not said:
            continue
        hits = len(wanted & said)
        if not hits:
            continue
        # Доля пункта, попавшая в реплику. Не Жаккар: реплика бывает длинной,
        # и делить на её длину — значит наказывать за подробный ответ.
        score = hits / len(wanted)
        if score > best:
            best_at, best = at, score
    return (best_at, best) if best >= FLOOR else (None, best)


def _thin(marks: list[dict], gap: float = 8.0) -> list[dict]:
    """Убирает метки, стоящие вплотную: на шкале они всё равно сольются."""
    kept: list[dict] = []
    for mark in marks:
        if kept and mark["at"] - kept[-1]["at"] < gap and mark["kind"] == kept[-1]["kind"]:
            continue
        kept.append(mark)
    return kept


def _stamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{int((seconds % 1) * 1000):03d}"
