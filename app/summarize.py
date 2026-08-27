"""Саммари, бриф, задачи и решения. Какая именно модель отвечает — решает
модуль ``llm``: Ollama, файл .gguf или OpenAI-совместимый сервер."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import compute, dates, llm, presets
from .llm import LLMError  # noqa: F401  — переэкспорт для остальных модулей
from .merge import Turn
from .presets import Preset
from .settings import Settings

Progress = Callable[[float, str], None]

# Профиль по умолчанию — рабочая встреча. Остальные живут в presets.py.
SECTIONS = presets.MEETING.sections
FINAL_TEMPLATE = presets.MEETING.template

SYSTEM = (
    "Ты — аналитик, который готовит рабочие материалы по расшифровкам встреч и "
    "записей. Пиши по-русски, по существу, деловым языком, без воды и без "
    "вступлений вроде «в этом тексте». Опирайся только на предоставленную "
    "расшифровку: ничего не додумывай и не добавляй фактов от себя. Если "
    "информации для раздела нет — так и напиши.\n"
    "Числа, суммы, проценты, сроки, названия и имена переноси дословно, как они "
    "прозвучали. Обобщение там, где в разговоре была конкретика, — это ошибка: "
    "«скидка до 13% при 10 000 XP» полезно, «предусмотрены скидки» бесполезно."
)


@dataclass
class Summary:
    markdown: str
    sections: dict[str, str]
    model: str
    preset: str = presets.DEFAULT
    tabs: list[tuple[str, str]] = field(default_factory=lambda: list(presets.MEETING.sections))
    tables: list[compute.Table] = field(default_factory=list)

    def get(self, key: str) -> str:
        return self.sections.get(key, "").strip()

    @property
    def csv(self) -> str:
        return compute.to_csv(self.tables) if self.tables else ""


# --- сборка текста для модели ------------------------------------------------

def transcript_for_llm(turns: list[Turn], names: dict[str, str] | None = None) -> str:
    names = names or {}
    lines = []
    for t in turns:
        who = names.get(t.speaker_key) or (
            "Неизвестный" if t.speaker is None else f"Спикер {t.speaker + 1}"
        )
        lines.append(f"[{_hhmm(t.start)}] {who}: {t.text}")
    return "\n".join(lines)


def _hhmm(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _chunks(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    lines = text.split("\n")
    out, cur, cur_len = [], [], 0
    for line in lines:
        if cur_len + len(line) > size and cur:
            out.append("\n".join(cur))
            # небольшой нахлёст, чтобы не терять контекст на границе
            cur, cur_len = cur[-3:], sum(len(x) for x in cur[-3:])
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


# --- основной сценарий -------------------------------------------------------

def summarize(turns: list[Turn], settings: Settings, meta: dict | None = None,
              names: dict[str, str] | None = None,
              progress: Progress | None = None,
              preset: Preset | None = None) -> Summary:
    meta = meta or {}
    preset = preset or presets.resolve(settings)
    backend = llm.build(settings)
    text = transcript_for_llm(turns, names)
    header = _meta_header(meta)
    parts = _chunks(text, int(settings["summary_chunk_chars"]))
    system = _system(preset)

    if len(parts) == 1:
        if progress:
            progress(0.2, f"{backend.name} готовит {preset.what}")
        final = backend.chat(system, _final_prompt(preset, header, text, notes=None))
    else:
        notes = []
        for i, part in enumerate(parts):
            if progress:
                progress(0.05 + 0.7 * i / len(parts),
                         f"Разбор части {i + 1} из {len(parts)} · {backend.name}")
            notes.append(backend.chat(
                system, _map_prompt(preset, header, part, i + 1, len(parts))))
        if progress:
            progress(0.8, f"Сборка итогового документа · {backend.name}")
        final = backend.chat(system, _final_prompt(
            preset, header, None, notes="\n\n---\n\n".join(notes)))

    # Второй заход: что важного не попало в документ. Модель может только
    # дописать пункт, переписать готовое ей не дают. Нужен весь текст сразу,
    # поэтому запускаем, только если расшифровка с документом влезают в
    # контекст: примерно два знака на токен для русского, с запасом на ответ.
    room = int(settings.get("llm_num_ctx", 32768)) * 2 - 8000
    if settings.get("summary_thorough", True) and len(text) + len(final) < room:
        if progress:
            progress(0.9, f"Проверяю, не упущено ли важное · {backend.name}")
        try:
            final = add_missed(final, preset, missed_items(backend, preset, text, final))
        except Exception:
            pass

    # Арифметику считаем сами: модель в ней ошибается уверенно и незаметно.
    # Проверяем любой профиль — цены могут прозвучать и на обычной встрече.
    result = compute.process(final, title=meta.get("title", ""))
    final, tables = result.markdown, result.tables

    # «Завтра» через неделю ничего не значит, а дату записи мы знаем — значит,
    # можем посчитать. Считает код: даты модели путают так же, как суммы.
    if settings.get("resolve_dates", True):
        final = dates.process(final, dates.parse_stamp(meta.get("recorded_at", ""))
                              or dates.parse_stamp(meta.get("processed_at", "")))

    if progress:
        progress(1.0, "Готово")
    return Summary(
        markdown=final,
        sections=parse_sections(final, preset.sections),
        model=backend.name,
        preset=preset.key,
        tabs=list(preset.sections),
        tables=tables,
    )


TITLE_SYSTEM = (
    "Ты придумываешь короткие названия для записей разговоров. Отвечай одной "
    "строкой по-русски: 2–5 слов о сути разговора, без кавычек, без точки в "
    "конце, без слов «саммари», «встреча», «созвон», «обсуждение». Не пиши "
    "ничего, кроме самого названия."
)


def suggest_title(text: str, settings: Settings) -> str:
    """Короткое название по сути разговора — вместо «Созвон 2026-08-27 13-32».

    Даты в названии не просим: её подставляет вызывающий код, и модель бы всё
    равно её выдумала. На вход идёт готовое саммари, а не вся расшифровка:
    так дешевле и точнее.
    """
    source = (text or "").strip()
    if len(source) < 40:
        return ""
    try:
        backend = llm.build(settings)
        answer = backend.chat(TITLE_SYSTEM, "Вот о чём была запись:\n\n"
                              + source[:4000] + "\n\nНазвание:")
    except Exception:
        return ""
    return clean_title(answer)


def clean_title(answer: str) -> str:
    """Модель любит добавить кавычки, «Название:» и точку — всё это убираем."""
    line = (answer or "").strip().split("\n")[0]
    line = re.sub(r"^\s*(название|title)\s*[:—-]\s*", "", line, flags=re.I)
    # Порядок важен: сначала точка в конце, потом кавычки, потом точка снова —
    # модель пишет и «Название».  и  "Название."
    for _ in range(2):
        line = re.sub(r"[.!?…]+$", "", line).strip()
        line = line.strip("\"'«»`*_ ").strip()
    # В имени файла косая черта и двоеточие ломают путь, а перевод строки —
    # весь список записей.
    line = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", line)
    line = re.sub(r"\s{2,}", " ", line).strip()
    words = line.split()
    if len(words) > 7:
        line = " ".join(words[:7])
    return line[:60]


# --- проверка на пропущенное -------------------------------------------------

MISSED_SYSTEM = (
    "Ты сверяешь готовый документ с расшифровкой и ищешь только то, что в "
    "документ не попало. Ничего не переписываешь и не повторяешь: твой ответ — "
    "список недостающего, строками вида «Раздел | пункт». Раздел бери из списка "
    "заголовков документа, слово в слово. Если всё существенное на месте, ответь "
    "одной строкой: ВСЁ НА МЕСТЕ."
)


def missed_items(backend, preset: Preset, text: str, document: str) -> list[tuple[str, str]]:
    """Спрашивает у модели, что важного не попало в документ.

    Отдельный проход, а не переписывание: так модель может только добавить, но
    не испортить и не потерять уже собранное. Сравнение gemma и qwen на одном
    созвоне показало, что каждая замечает своё — значит, и одна модель со
    второго захода видит то, что пропустила с первого.
    """
    titles = [title for _, title in preset.sections]
    prompt = f"""РАСШИФРОВКА:
{text}

ГОТОВЫЙ ДОКУМЕНТ:
{document}

Разделы документа: {", ".join(titles)}.

Что важного из расшифровки не попало в документ? Считаются: названные числа,
суммы, проценты, сроки, условия, договорённости, задачи, возражения и риски.
Не считается: пересказ того, что уже написано другими словами.

Ответ — не больше десяти строк вида «Раздел | пункт», без пояснений."""
    answer = backend.chat(MISSED_SYSTEM, prompt)
    if "ВСЁ НА МЕСТЕ" in answer.upper():
        return []

    known = {title.lower(): title for title in titles}
    out: list[tuple[str, str]] = []
    for raw in answer.split("\n"):
        line = re.sub(r"^[\s\-*•\d.)]+", "", raw).strip()
        # Строка markdown-таблицы — это не «раздел | пункт», а кусок документа,
        # который модель зачем-то процитировала.
        if "|" not in line or raw.strip().startswith("|") or line.count("|") > 2:
            continue
        head, _, body = line.partition("|")
        head = re.sub(r"[*_`#]+", "", head).strip().lower()
        body = body.strip(" -–—*_`")
        if len(head) < 3 or len(body) < 8:
            continue
        title = known.get(head)
        if title is None:
            # Частичное совпадание — но только осмысленное: пустой или слишком
            # короткий заголовок иначе «подходит» к любому разделу.
            title = next((known[k] for k in known if k in head or head in k), None)
        if title is None:
            continue
        out.append((title, body))
    return out[:10]


def add_missed(document: str, preset: Preset, items: list[tuple[str, str]]) -> str:
    """Дописывает найденное в конец нужных разделов, не трогая остальное."""
    if not items:
        return document
    # Ключи сравниваем без учёта регистра: заголовок в документе и название
    # раздела в ответе модели совпадают не всегда буква в букву.
    by_title: dict[str, list[str]] = {}
    for title, body in items:
        by_title.setdefault(title.strip().lower(), []).append(body)

    lines = document.split("\n")
    out: list[str] = []
    current: str | None = None

    def flush() -> None:
        for body in by_title.pop((current or "").lower(), []):
            # Ставим в конец раздела, отдельным пунктом: видно, что добавлено.
            out.append(f"- {body}")

    for line in lines:
        heading = re.match(r"^\s*##\s+(?!#)(.+?)\s*$", line)
        if heading:
            flush()
            if out and out[-1].strip():
                out.append("")
            current = re.sub(r"[*_`:]+", "", heading.group(1)).strip()
        out.append(line)
    flush()
    return "\n".join(out)


def _system(preset: Preset) -> str:
    return SYSTEM + ("\n\n" + preset.rules.strip() if preset.rules.strip() else "")


def _meta_header(meta: dict) -> str:
    bits = []
    if meta.get("title"):
        bits.append(f"Название файла: {meta['title']}")
    if meta.get("duration"):
        bits.append(f"Длительность: {_hhmm(meta['duration'])}")
    if meta.get("speakers"):
        bits.append(f"Участников распознано: {meta['speakers']}")
    return "\n".join(bits)


def _map_prompt(preset: Preset, header: str, part: str, idx: int, total: int) -> str:
    wanted = "\n".join(f"- {title}" for _, title in preset.sections)
    return f"""{header}

Ниже — фрагмент {idx} из {total} расшифровки записи.

Выпиши по этому фрагменту подробные заметки — всё, что понадобится, чтобы потом
собрать итоговый документ с такими разделами:
{wanted}

Не сокращай и не обобщай: эти заметки — единственное, что дойдёт до итогового
документа, сам фрагмент больше никто не увидит. Обязательно сохраняй дословно
все цифры, суммы, ставки, количества, даты, сроки, имена и названия. Ничего не
выдумывай: чего в фрагменте нет — того не пиши.

РАСШИФРОВКА (фрагмент {idx}/{total}):
{part}"""


def _final_prompt(preset: Preset, header: str, text: str | None,
                  notes: str | None) -> str:
    source = (
        f"РАСШИФРОВКА:\n{text}" if text is not None
        else f"РАБОЧИЕ ЗАМЕТКИ ПО ФРАГМЕНТАМ (по порядку):\n{notes}"
    )
    rules = preset.rules.strip()
    return f"""{header}

{source}

Составь итоговый документ строго по этому шаблону, сохраняя заголовки без изменений:

{preset.template}

Требования: только факты из источника; конкретные формулировки вместо общих.
Срок — это не только дата: «завтра», «до пятницы», «на следующей неделе»,
«к концу месяца» — это тоже сроки, переноси их как сказано. Прочерк ставь
только там, где срок или ответственный действительно не прозвучали.
{rules}
Не добавляй никаких разделов, кроме перечисленных, и никакого текста до первого
заголовка."""


def parse_sections(markdown: str,
                   sections: list[tuple[str, str]] | None = None) -> dict[str, str]:
    """Режет ответ модели по заголовкам ## на именованные разделы."""
    sections = sections or SECTIONS
    result: dict[str, str] = {}
    titles = {title.lower(): key for key, title in sections}
    current: str | None = None
    buf: list[str] = []

    for line in markdown.split("\n"):
        m = re.match(r"^\s*##\s+(?!#)(.+?)\s*$", line)
        if m:
            if current:
                result[current] = "\n".join(buf).strip()
            heading = re.sub(r"[*_`:]+", "", m.group(1)).strip().lower()
            current = titles.get(heading)
            if current is None:
                for known, key in titles.items():
                    if known in heading or heading in known:
                        current = key
                        break
            buf = []
            if current is None:
                current = "_extra"
            continue
        buf.append(line)
    if current:
        result[current] = "\n".join(buf).strip()

    result.pop("_extra", None)
    if not result:
        result[sections[0][0] if sections else "brief"] = markdown.strip()
    return result
