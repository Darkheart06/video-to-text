"""Что именно приложение готовит из записи.

Одна и та же расшифровка нужна для разного: встречу хочется свести в бриф,
надиктованный список работ — в смету с итогом, а голосовую заметку просто
разложить по полочкам. Профиль задаёт правила для модели и шаблон документа.

Свои правила можно написать прямо в настройках — они подставляются в тот же
механизм, без всякого программирования.

Профили есть на обоих языках, и это не перевод ради перевода: заголовки из
шаблона попадают в готовый документ и во вкладки окна, а по ним же модель
раскладывает ответ. Ключи разделов («summary», «tasks») общие для языков —
поэтому запись, сделанная по-русски, откроется в английском окне с
английскими подписями.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import i18n


@dataclass
class Preset:
    key: str
    title: str
    hint: str
    rules: str                       # правила для модели
    template: str                    # шаблон итогового документа
    sections: list[tuple[str, str]]  # ключ раздела -> заголовок в документе
    tables: list[str] = field(default_factory=list)   # разделы, где ждём таблицу с ценами
    what: str = "документ"           # что именно готовим — для строки прогресса


RU: dict[str, Preset] = {}
EN: dict[str, Preset] = {}

# --- русские профили ---------------------------------------------------------

RU["meeting"] = Preset(
    key="meeting",
    title="Встреча или созвон",
    hint="Саммари, бриф, задачи, решения и риски — как для рабочей встречи.",
    rules=(
        "Опирайся только на расшифровку: ничего не додумывай. "
        "В задачах используй глагол действия. Если ответственный или срок "
        "не назван — ставь «—»."
    ),
    template="""## Краткое саммари
- (5–8 пунктов: о чём была запись и чем закончилась)

## Бриф
### Контекст
### Обсуждённые темы
### Ключевые тезисы

## Решения
- (что решили; если решений не было — «Решений не зафиксировано»)

## Задачи
| Задача | Ответственный | Срок |
|---|---|---|
(если задач нет — напиши «Задач не зафиксировано» вместо таблицы)

## Открытые вопросы и риски
- (что осталось нерешённым; если ничего — «Не зафиксировано»)""",
    what="саммари и бриф",
    sections=[
        ("summary", "Краткое саммари"),
        ("brief", "Бриф"),
        ("decisions", "Решения"),
        ("tasks", "Задачи"),
        ("risks", "Открытые вопросы и риски"),
    ],
)

RU["estimate"] = Preset(
    key="estimate",
    title="Смета по надиктованному",
    hint="Список работ со ставками и часами превращается в таблицу с итогом. "
         "Считает приложение, а не модель — в арифметике она ненадёжна.",
    rules=(
        "Из записи надо собрать смету. Каждая названная работа — отдельная "
        "строка таблицы.\n"
        "В колонки «Количество» и «Ставка» ставь ТОЛЬКО числа, без слов и "
        "знаков валюты. Единицу измерения (час, шт, день) пиши в свою колонку.\n"
        "Колонку «Стоимость» оставляй пустой — её посчитает приложение. "
        "Строку «Итого» не добавляй.\n"
        "Если для работы названа сразу общая сумма без ставки — впиши её "
        "в «Ставку», а в «Количество» поставь 1.\n"
        "Если что-то названо неразборчиво или без цены — вынеси это "
        "в «Что уточнить», а не выдумывай числа."
    ),
    template="""## Работы
| Работа | Количество | Единица | Ставка | Стоимость |
|---|---|---|---|---|

## Условия
- (сроки, порядок оплаты, что входит и не входит — если про это говорилось)

## Что уточнить
- (работы без цены, неясные формулировки; если всё ясно — «Всё определено»)

## Кратко
- (2–4 пункта: о чём смета)""",
    what="смету",
    sections=[
        ("works", "Работы"),
        ("terms", "Условия"),
        ("open", "Что уточнить"),
        ("summary", "Кратко"),
    ],
    tables=["works"],
)

RU["note"] = Preset(
    key="note",
    title="Голосовая заметка",
    hint="Раскладывает надиктованное по полочкам: мысли, задачи, списки.",
    rules=(
        "Это надиктованная заметка одного человека, а не разговор. "
        "Сохраняй порядок мыслей и формулировки автора, не пересказывай "
        "своими словами. Ничего не добавляй от себя."
    ),
    template="""## Главное
- (3–6 пунктов: суть заметки)

## Подробно
### (тема)
- (мысли по теме, в порядке, в котором они прозвучали)

## Задачи
| Задача | Срок |
|---|---|
(если задач нет — «Задач нет»)

## Списки и цифры
- (перечисления, суммы, даты, названия — всё, что прозвучало конкретного;
  если ничего такого не было — «Не прозвучало»)""",
    what="заметку",
    sections=[
        ("summary", "Главное"),
        ("detail", "Подробно"),
        ("tasks", "Задачи"),
        ("facts", "Списки и цифры"),
    ],
)

RU["interview"] = Preset(
    key="interview",
    title="Интервью с пользователем",
    hint="Боли, цитаты, наблюдения и выводы — для исследований.",
    rules=(
        "Это интервью. Цитаты приводи дословно и в кавычках — они ценнее "
        "пересказа. Не обобщай там, где человек говорил конкретно."
    ),
    template="""## Кратко
- (3–6 пунктов: что выяснили)

## Боли и трудности
- (с чем человек мучается, своими его словами)

## Прямые цитаты
- «(цитата)»

## Что он делает сейчас
- (как решает задачу без нас)

## Выводы и гипотезы
- (что из этого следует; помечай, где это уже домысел)""",
    what="сводку по интервью",
    sections=[
        ("summary", "Кратко"),
        ("pains", "Боли и трудности"),
        ("quotes", "Прямые цитаты"),
        ("current", "Что он делает сейчас"),
        ("insights", "Выводы и гипотезы"),
    ],
)

# --- английские профили ------------------------------------------------------

EN["meeting"] = Preset(
    key="meeting",
    title="Meeting or call",
    hint="Summary, brief, tasks, decisions and risks — as for a working meeting.",
    rules=(
        "Rely on the transcript alone: invent nothing. Start every task with an "
        "action verb. If the owner or the deadline was not named, put “—”."
    ),
    template="""## Summary
- (5–8 bullets: what the recording was about and how it ended)

## Brief
### Context
### Topics discussed
### Key points

## Decisions
- (what was decided; if nothing was — “No decisions recorded”)

## Tasks
| Task | Owner | Deadline |
|---|---|---|
(if there are no tasks, write “No tasks recorded” instead of the table)

## Open questions and risks
- (what is still unresolved; if nothing — “Nothing recorded”)""",
    what="the summary and brief",
    sections=[
        ("summary", "Summary"),
        ("brief", "Brief"),
        ("decisions", "Decisions"),
        ("tasks", "Tasks"),
        ("risks", "Open questions and risks"),
    ],
)

EN["estimate"] = Preset(
    key="estimate",
    title="Estimate from dictation",
    hint="A dictated list of jobs with rates and hours becomes a table with a total. "
         "The app does the arithmetic, not the model — it is unreliable at it.",
    rules=(
        "Build a cost estimate from the recording. Every job that was named is its "
        "own table row.\n"
        "Put ONLY numbers in the “Quantity” and “Rate” columns — no words, no "
        "currency signs. The unit (hour, item, day) goes in its own column.\n"
        "Leave the “Amount” column empty — the app computes it. Do not add a "
        "“Total” row.\n"
        "If a job was quoted as a lump sum with no rate, put that sum in “Rate” and "
        "1 in “Quantity”.\n"
        "If something was said unclearly or without a price, put it under “To "
        "clarify” instead of inventing numbers."
    ),
    template="""## Work
| Job | Quantity | Unit | Rate | Amount |
|---|---|---|---|---|

## Terms
- (deadlines, payment, what is and is not included — if it was discussed)

## To clarify
- (jobs with no price, unclear wording; if everything is clear — “All clear”)

## In short
- (2–4 bullets: what this estimate covers)""",
    what="the estimate",
    sections=[
        ("works", "Work"),
        ("terms", "Terms"),
        ("open", "To clarify"),
        ("summary", "In short"),
    ],
    tables=["works"],
)

EN["note"] = Preset(
    key="note",
    title="Voice note",
    hint="Sorts what was dictated into thoughts, tasks and lists.",
    rules=(
        "This is one person's dictated note, not a conversation. Keep the order of "
        "the thoughts and the author's own wording, do not retell it in your own "
        "words. Add nothing of your own."
    ),
    template="""## The gist
- (3–6 bullets: what the note is about)

## In detail
### (topic)
- (thoughts on the topic, in the order they were said)

## Tasks
| Task | Deadline |
|---|---|
(if there are no tasks — “No tasks”)

## Lists and numbers
- (enumerations, sums, dates, names — everything specific that was said;
  if there was nothing — “Nothing specific”)""",
    what="the note",
    sections=[
        ("summary", "The gist"),
        ("detail", "In detail"),
        ("tasks", "Tasks"),
        ("facts", "Lists and numbers"),
    ],
)

EN["interview"] = Preset(
    key="interview",
    title="User interview",
    hint="Pains, quotes, observations and conclusions — for research.",
    rules=(
        "This is an interview. Quote verbatim and in quotation marks — quotes are "
        "worth more than a retelling. Do not generalise where the person was specific."
    ),
    template="""## In short
- (3–6 bullets: what was learned)

## Pains and frustrations
- (what the person struggles with, in their own words)

## Direct quotes
- “(quote)”

## What they do today
- (how they solve it without us)

## Conclusions and hypotheses
- (what follows from this; mark where it is already a guess)""",
    what="the interview digest",
    sections=[
        ("summary", "In short"),
        ("pains", "Pains and frustrations"),
        ("quotes", "Direct quotes"),
        ("current", "What they do today"),
        ("insights", "Conclusions and hypotheses"),
    ],
)

BY_LANG = {"ru": RU, "en": EN}

# Совместимость и «профиль по умолчанию» для кода, которому язык не важен.
MEETING, ESTIMATE, NOTE, INTERVIEW = (RU["meeting"], RU["estimate"],
                                      RU["note"], RU["interview"])
BUILTIN = RU
DEFAULT = "meeting"

CUSTOM_KEY = "custom"
CUSTOM = {
    "ru": {
        "title": "Свои правила",
        "hint": ("Напишите словами, что нужно получить, — и задайте шаблон "
                 "заголовками ##. Приложение само разложит ответ по вкладкам."),
        "what": "документ",
        "fallback": "Кратко",
        "example": """## Кратко
- (о чём запись)

## Таблица
| Пункт | Количество | Ставка | Стоимость |
|---|---|---|---|

## Прочее
- (всё остальное)""",
    },
    "en": {
        "title": "Your own rules",
        "hint": ("Describe in plain words what you need, and set the shape with ## "
                 "headings. The app turns every heading into a tab."),
        "what": "the document",
        "fallback": "In short",
        "example": """## In short
- (what the recording is about)

## Table
| Item | Quantity | Rate | Amount |
|---|---|---|---|

## Anything else
- (everything that did not fit above)""",
    },
}

CUSTOM_TITLE = CUSTOM["ru"]["title"]
CUSTOM_HINT = CUSTOM["ru"]["hint"]
CUSTOM_EXAMPLE = CUSTOM["ru"]["example"]


def _lang(value: str = "") -> str:
    return i18n.pick(value, i18n.current())


def builtin(lang: str = "") -> dict[str, Preset]:
    return BY_LANG.get(_lang(lang), EN)


def custom_example(lang: str = "") -> str:
    return CUSTOM[_lang(lang)]["example"]


def _sections_from_template(template: str, lang: str) -> list[tuple[str, str]]:
    """Достаёт заголовки ## из пользовательского шаблона, чтобы разложить
    ответ модели по вкладкам без всякой настройки."""
    found: list[tuple[str, str]] = []
    for i, line in enumerate(template.split("\n")):
        m = re.match(r"^\s*##\s+(?!#)(.+?)\s*$", line)
        if m:
            found.append((f"s{i}", m.group(1).strip()))
    return found or [("summary", CUSTOM[lang]["fallback"])]


def build_custom(rules: str, template: str, lang: str = "") -> Preset:
    lang = _lang(lang)
    words = CUSTOM[lang]
    template = (template or "").strip() or words["example"]
    sections = _sections_from_template(template, lang)
    return Preset(
        key=CUSTOM_KEY, title=words["title"], hint=words["hint"],
        rules=(rules or "").strip(),
        template=template,
        what=words["what"],
        sections=sections,
        # Пересчитываем таблицы во всех разделах: пользователь мог попросить
        # смету своими словами, и считать всё равно должны мы, а не модель.
        tables=[key for key, _ in sections],
    )


def language_of(settings) -> str:
    """Язык документов — работает и со словарём, и с Settings."""
    ui = i18n.pick(settings.get("ui_language", "auto"))
    return i18n.pick(settings.get("doc_language", "auto"), ui)


def resolve(settings) -> Preset:
    lang = language_of(settings)
    key = settings.get("preset", DEFAULT)
    if key == CUSTOM_KEY:
        return build_custom(settings.get("custom_rules", ""),
                            settings.get("custom_template", ""), lang)
    return builtin(lang).get(key, builtin(lang)["meeting"])


def tabs_for(keys: list[str], lang: str = "") -> list[tuple[str, str]]:
    """Подбирает подписи вкладок по ключам разделов.

    Нужно для старых записей из архива: там сохранены только ключи, а какой
    профиль их сделал — неизвестно. Берём профиль, который совпал сильнее всех.
    Ключи разделов одинаковы на обоих языках, поэтому русская запись спокойно
    открывается в английском окне.
    """
    keys = [k for k in keys if k]
    best, score = None, -1
    for preset in builtin(lang).values():
        hit = len({k for k, _ in preset.sections} & set(keys))
        if hit > score:
            best, score = preset, hit
    titles = dict(best.sections) if best else {}
    return [(k, titles.get(k) or k.replace("_", " ").capitalize()) for k in keys]


def catalogue(lang: str = "") -> list[dict]:
    """Список профилей для интерфейса."""
    lang = _lang(lang)
    items = [{"key": p.key, "title": p.title, "hint": p.hint}
             for p in builtin(lang).values()]
    items.append({"key": CUSTOM_KEY, "title": CUSTOM[lang]["title"],
                  "hint": CUSTOM[lang]["hint"]})
    return items
