"""Превращение «завтра» и «до пятницы» в настоящие даты.

В разговоре сроки называют словами, и через неделю «завтра» в списке задач уже
ничего не значит. Дату мы знаем — это дата самой записи, — так что посчитать
можно точно.

Считает код, а не модель: с арифметикой дат языковые модели ошибаются так же
уверенно, как с умножением, и «завтра» превращается то в позавчера, то в
следующий вторник. Здесь же всё однозначно.

Исходные слова остаются на месте, дата приписывается рядом: «завтра
(28 августа)». Так видно и что было сказано, и что это значит.

Русский и английский разбираются одним и тем же кодом: слова разные, а
арифметика одна. Язык нужен только для того, чтобы правильно написать дату —
«28 августа» или «28 August».
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from . import i18n

MONTHS = {
    "ru": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"),
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
}

# Слова, которыми называют день недели, — в разных падежах.
WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
    "пятниц": 4, "суббот": 5, "воскресен": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Заголовки колонок, в которых имеет смысл искать срок.
DEADLINE_WORDS = ("срок", "дата", "когда", "дедлайн", "deadline", "due", "date",
                  "when", "by when")

# Ничего не значащие прочерки — их не трогаем.
DASHES = {"—", "-", "–", "n/a", "none", "tbd"}


def human(day: date, lang: str = "") -> str:
    lang = i18n.pick(lang, i18n.current())
    months = MONTHS.get(lang) or MONTHS["en"]
    name = months[day.month - 1]
    return f"{day.day} {name}" if lang == "ru" else f"{name} {day.day}"


def resolve(text: str, base: date, lang: str = "") -> str:
    """Дописывает дату к относительному сроку в одной ячейке."""
    value = (text or "").strip()
    if not value or value.lower() in DASHES:
        return text
    # Дата уже названа — второй раз не пишем.
    lowered = value.lower()
    if re.search(r"\d{1,2}[.\s/-]\d{1,2}", value):
        return text
    if any(m.lower() in lowered for m in MONTHS["ru"] + MONTHS["en"]):
        return text

    day = _day_for(lowered, base)
    if day is None:
        return text
    return f"{value} ({human(day, lang)})"


def _day_for(lowered: str, base: date) -> date | None:
    if "послезавтра" in lowered or "day after tomorrow" in lowered:
        return base + timedelta(days=2)
    if "завтра" in lowered or "tomorrow" in lowered:
        return base + timedelta(days=1)
    if "сегодня" in lowered or "today" in lowered:
        return base
    # «конец», «конца», «концу» — падежи, ловим по корню.
    if ("конц" in lowered and "месяц" in lowered) or "end of the month" in lowered \
            or "end of month" in lowered:
        return _month_end(base)
    if ("следующ" in lowered and "недел" in lowered) or "next week" in lowered:
        # Понедельник следующей недели — то, что обычно имеют в виду.
        return base + timedelta(days=7 - base.weekday())
    if ("конц" in lowered and "недел" in lowered) or "этой недел" in lowered \
            or "end of the week" in lowered or "this week" in lowered:
        return base + timedelta(days=(4 - base.weekday()) % 7)

    for word, number in WEEKDAYS.items():
        if word in lowered:
            ahead = (number - base.weekday()) % 7
            # «В пятницу», сказанное в пятницу, — это обычно следующая пятница.
            return base + timedelta(days=ahead or 7)
    return None


def _month_end(base: date) -> date:
    if base.month == 12:
        return date(base.year, 12, 31)
    return date(base.year, base.month + 1, 1) - timedelta(days=1)


def process(markdown: str, base: date | None, lang: str = "") -> str:
    """Проставляет даты в колонках со сроками.

    Только в таблицах: в связном тексте «до пятницы» может быть цитатой или
    частью рассуждения, и дописывать туда дату — значит менять чужие слова.
    """
    if base is None:
        return markdown
    lines = (markdown or "").split("\n")
    out: list[str] = []
    columns: list[int] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            columns = []
            out.append(line)
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if set(stripped) <= set("-:| "):
            out.append(line)
            continue

        header = i + 1 < len(lines) and set(lines[i + 1].strip()) <= set("-:| ") \
            and lines[i + 1].strip().startswith("|")
        if header:
            columns = [n for n, cell in enumerate(cells)
                       if any(word in cell.lower() for word in DEADLINE_WORDS)]
            out.append(line)
            continue

        if not columns:
            out.append(line)
            continue
        for n in columns:
            if n < len(cells):
                cells[n] = resolve(cells[n], base, lang)
        out.append("| " + " | ".join(cells) + " |")

    return "\n".join(out)


def parse_stamp(value: str) -> date | None:
    """Достаёт дату из «2026-08-27 13-32» или «2026-08-27 13:32»."""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
