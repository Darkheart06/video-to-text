"""Саммари, бриф, задачи и решения. Какая именно модель отвечает — решает
модуль ``llm``: Ollama, файл .gguf или OpenAI-совместимый сервер."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import compute, llm, presets
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
    "информации для раздела нет — так и напиши."
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

    # Арифметику считаем сами: модель в ней ошибается уверенно и незаметно.
    # Проверяем любой профиль — цены могут прозвучать и на обычной встрече.
    result = compute.process(final, title=meta.get("title", ""))
    final, tables = result.markdown, result.tables

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

Выпиши по этому фрагменту сжатые заметки — всё, что понадобится, чтобы потом
собрать итоговый документ с такими разделами:
{wanted}

Обязательно сохраняй дословно все цифры, суммы, ставки, количества, даты,
имена и названия. Ничего не выдумывай: чего в фрагменте нет — того не пиши.

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
