"""Пересчёт таблиц, которые собрала модель.

Языковые модели уверенно ошибаются в арифметике: 12 часов по 3000 у них
запросто превращаются в 34 000. Поэтому модель только раскладывает
услышанное по колонкам, а умножение и итог считает этот модуль.

Работает по смыслу заголовков, а не по жёсткой схеме: если в таблице нашлись
колонки «сколько» и «почём», появится колонка стоимости и строка «Итого».
Если таких колонок нет — таблица остаётся как есть.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from . import i18n

# Как называются колонки. Порядок важен: сначала более узкие варианты.
QTY_WORDS = ("количество", "кол-во", "колво", "часы", "часов", "часа", "час",
             "объём", "объем", "шт", "штук", "qty", "quantity", "hours")
RATE_WORDS = ("ставка", "цена", "тариф", "стоимость часа", "цена за единицу",
              "за единицу", "за час", "rate", "price")
SUM_WORDS = ("стоимость", "сумма", "итого", "всего", "amount", "total", "sum")
UNIT_WORDS = ("единица", "ед", "ед.", "единицы", "unit")

TOTAL_LABELS = ("итого", "всего", "total", "итог")

# Как подписать посчитанную строку — на языке документа.
TOTAL_WORD = {"ru": "Итого", "en": "Total"}

CURRENCY_SIGNS = {"₽": "₽", "руб": "₽", "р.": "₽", "rub": "₽",
                  "$": "$", "usd": "$", "€": "€", "eur": "€"}


@dataclass
class Table:
    """Готовая к выгрузке таблица: заголовки, строки и итог."""
    title: str
    header: list[str]
    rows: list[list[str]]
    total: float | None = None
    currency: str = ""
    changed: bool = False
    sum_index: int = -1
    qty_index: int = -1
    rate_index: int = -1

    @property
    def numeric_columns(self) -> set[int]:
        return {i for i in (self.qty_index, self.rate_index, self.sum_index) if i >= 0}


@dataclass
class Result:
    markdown: str
    tables: list[Table] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(t.changed for t in self.tables)


# --- разбор чисел ------------------------------------------------------------

_NUM = re.compile(r"-?\d[\d\s  ']*(?:[.,]\d+)?")


def parse_number(cell: str) -> float | None:
    """Достаёт число из ячейки: «1 200,50 ₽», «12 часов», «3 000» — всё годится."""
    if not cell:
        return None
    text = cell.replace(" ", " ").replace(" ", " ").strip()
    text = re.sub(r"[*_`]", "", text)
    m = _NUM.search(text)
    if not m:
        return None
    raw = m.group(0)
    raw = re.sub(r"[\s'  ]", "", raw)
    # «1,5» — это полтора, а «1,500» модель почти наверняка имела в виду как 1500
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")
    elif "," in raw:
        head, _, tail = raw.partition(",")
        raw = f"{head}.{tail}" if len(tail) <= 2 else head + tail
    try:
        return float(raw)
    except ValueError:
        return None


def detect_currency(cells: list[str]) -> str:
    joined = " ".join(cells).lower()
    for token, sign in CURRENCY_SIGNS.items():
        if token in joined:
            return sign
    return ""


def format_number(value: float) -> str:
    """Тысячи разделяем узким пробелом, хвост из нулей не пишем."""
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 0.005:
        body = f"{int(round(rounded)):,}".replace(",", " ")
    else:
        body = f"{rounded:,.2f}".replace(",", " ").replace(".", ",")
    return body


# --- разбор markdown-таблиц --------------------------------------------------

def _is_separator(line: str) -> bool:
    stripped = line.strip().strip("|")
    return bool(stripped) and set(stripped) <= set("-: |")


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _column(header: list[str], words: tuple[str, ...]) -> int | None:
    lowered = [re.sub(r"[*_`]", "", h).strip().lower() for h in header]
    for word in words:
        for i, name in enumerate(lowered):
            if name == word:
                return i
    for word in words:
        for i, name in enumerate(lowered):
            if word in name:
                return i
    return None


def _render_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


# --- основной сценарий -------------------------------------------------------

def process(markdown: str, title: str = "", lang: str = "") -> Result:
    """Находит в тексте таблицы, пересчитывает стоимость и добавляет «Итого»."""
    lines = markdown.split("\n")
    out: list[str] = []
    tables: list[Table] = []
    i = 0
    current_title = title

    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^\s*#{2,4}\s+(.+?)\s*$", line)
        if heading:
            current_title = re.sub(r"[*_`]", "", heading.group(1)).strip()

        if (line.strip().startswith("|") and i + 1 < len(lines)
                and _is_separator(lines[i + 1])):
            block, i = _collect(lines, i)
            table = _recalc(block, current_title or title or "Таблица")
            if table is None:
                out.extend(block)
            else:
                tables.append(table)
                out.extend(_to_markdown(table, lang))
            continue

        out.append(line)
        i += 1

    return Result(markdown="\n".join(out), tables=tables)


def _collect(lines: list[str], start: int) -> tuple[list[str], int]:
    i = start
    block: list[str] = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        block.append(lines[i])
        i += 1
    return block, i


def _recalc(block: list[str], title: str) -> Table | None:
    header = _cells(block[0])
    body = [_cells(row) for row in block[2:]]
    body = [r for r in body if any(c.strip() for c in r)]
    if not body:
        return None

    # Строку «Итого», если модель её всё-таки написала, выкидываем: посчитаем сами.
    body = [r for r in body
            if not (r and re.sub(r"[*_`:]", "", r[0]).strip().lower() in TOTAL_LABELS)]
    if not body:
        return None

    qty_i = _column(header, QTY_WORDS)
    rate_i = _column(header, RATE_WORDS)
    sum_i = _column(header, SUM_WORDS)
    # «Стоимость» бывает и ставкой («стоимость часа»), и суммой — не путаем
    if sum_i is not None and sum_i == rate_i:
        sum_i = None
    if qty_i is not None and qty_i in (rate_i, sum_i):
        qty_i = None

    if qty_i is None or rate_i is None:
        return None

    width = len(header)
    if sum_i is None:
        header = header + ["Стоимость"]
        sum_i = width
        width += 1

    currency = detect_currency([r[rate_i] for r in body if len(r) > rate_i]
                               + [header[rate_i], header[sum_i]])

    rows: list[list[str]] = []
    total = 0.0
    counted = False
    changed = False

    for row in body:
        row = (row + [""] * width)[:width]
        qty = parse_number(row[qty_i])
        rate = parse_number(row[rate_i])
        if qty is None and rate is not None:
            qty = 1.0
            row[qty_i] = "1"
            changed = True
        if qty is not None and rate is not None:
            value = qty * rate
            fresh = format_number(value) + (f" {currency}" if currency else "")
            if row[sum_i].strip() != fresh:
                changed = True
            row[sum_i] = fresh
            total += value
            counted = True
        rows.append([c.strip() for c in row])

    if not counted:
        return None

    return Table(title=title, header=[h.strip() for h in header], rows=rows,
                 total=total, currency=currency, changed=changed, sum_index=sum_i,
                 qty_index=qty_i, rate_index=rate_i)


def _total_word(lang: str) -> str:
    return TOTAL_WORD.get(i18n.pick(lang, i18n.current()), TOTAL_WORD["en"])


def _to_markdown(table: Table, lang: str = "") -> list[str]:
    lines = [_render_row(table.header),
             "|" + "|".join(["---"] * len(table.header)) + "|"]
    lines += [_render_row(r) for r in table.rows]
    if table.total is not None:
        sum_i = table.sum_index if table.sum_index >= 0 else len(table.header) - 1
        cells = [""] * len(table.header)
        cells[0] = f"**{_total_word(lang)}**"
        cells[sum_i] = "**" + format_number(table.total) + \
                       (f" {table.currency}" if table.currency else "") + "**"
        lines.append(_render_row(cells))
    return lines


# --- выгрузка ----------------------------------------------------------------

def to_csv(tables: list[Table], lang: str = "") -> str:
    """Одна CSV на все таблицы: между ними пустая строка и заголовок раздела.

    Числа выгружаем голыми — без знака валюты и разделителей тысяч, иначе
    Excel и Numbers считают их текстом и не дают ничего сложить.
    """
    buf = io.StringIO()
    # Excel на маке открывает CSV по разделителю из локали — точка с запятой
    # надёжнее запятой, а BOM не даёт ему испортить кириллицу.
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    for n, table in enumerate(tables):
        if n:
            writer.writerow([])
        writer.writerow([table.title])
        writer.writerow(_csv_header(table))
        numeric = table.numeric_columns
        for row in table.rows:
            writer.writerow([_csv_cell(c, i in numeric) for i, c in enumerate(row)])
        if table.total is not None:
            tail = [""] * len(table.header)
            tail[0] = _total_word(lang)
            sum_i = table.sum_index if table.sum_index >= 0 else len(table.header) - 1
            tail[sum_i] = _csv_number(table.total)
            writer.writerow(tail)
    return "\ufeff" + buf.getvalue()


def _csv_header(table: Table) -> list[str]:
    header = list(table.header)
    if table.currency:
        for i in (table.rate_index, table.sum_index):
            if 0 <= i < len(header) and table.currency not in header[i]:
                header[i] = f"{_plain(header[i])}, {table.currency}"
    return [_plain(h) for h in header]


def _csv_cell(cell: str, numeric: bool) -> str:
    if numeric:
        value = parse_number(cell)
        if value is not None:
            return _csv_number(value)
    return _plain(cell)


def _csv_number(value: float) -> str:
    """Дробную часть отделяем запятой — так её понимают русская локаль Excel
    и Numbers, а точка в них превращает число в дату."""
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 0.005:
        return str(int(round(rounded)))
    return f"{rounded:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _plain(cell: str) -> str:
    """Для таблиц числа нужны без разметки и без узких пробелов."""
    text = re.sub(r"[*_`]", "", cell).strip()
    return text.replace(" ", " ")
