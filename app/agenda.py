"""Расписание созвонов: системный календарь и свои события в одном списке.

Встречи прилетают в разные календари — рабочий Gmail, личный Яндекс, чужой
Outlook, — и человек, договариваясь о времени прямо на созвоне, не помнит их
все. Здесь они сводятся в один список, где видно, что на что накладывается.

**События берём из системного Календаря macOS, а не из API сервисов.** Gmail,
Outlook и Яндекс уже синхронизируются туда: Google и Outlook штатно, Яндекс по
CalDAV. Одно системное разрешение заменяет три интеграции с ревью, токенами и
постоянным сопровождением — и, главное, ничего не уходит с машины.

Свои события живут рядом, в `.work/agenda.json`: не всякая договорённость
доходит до календаря, а «созвонимся во вторник в три» сказанное на встрече —
уже созвон. Такое событие можно потом одной кнопкой отправить в настоящий
календарь, чтобы его увидели остальные.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import dates, i18n, media
from .settings import WORK_DIR

HELPER = Path(os.environ.get("V2T_HELPER") or (media.BUNDLED_BIN / "v2t-capture"))
STORE = WORK_DIR / "agenda.json"

# На сколько дней вперёд смотрим. Две недели — горизонт, в котором человек
# ещё что-то планирует; дальше список превращается в архив намерений.
HORIZON = 14

# Системный календарь опрашиваем не чаще, чем раз в эти секунды: помощник
# поднимается заново на каждый вызов, а события меняются редко.
FRESH = 90

# Ссылки, по которым видно, что событие — созвон, а не поход к врачу.
CALL_MARKS = (
    "meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com",
    "telemost.yandex", "ktalk.ru", "kontur.ru", "talk.contour", "webinar.ru",
    "jitsi", "whereby.com", "discord.gg", "meet.jit.si", "vk.com/call",
    "salutejazz", "sberjazz", "dion.vc", "videomost", "trueconf",
)

_seen: dict[str, tuple[float, list]] = {}


# --- системный календарь -----------------------------------------------------

def helper_ready() -> bool:
    return HELPER.exists()


def status() -> dict:
    """Дали ли доступ к календарю и можно ли в него писать."""
    if not helper_ready():
        return {"granted": False, "writable": False, "error": i18n.t("rec.helper_missing")}
    return _ask(["calendar-status"], {}) or {"granted": False, "writable": False}


def request() -> dict:
    """Показывает системный запрос доступа. Ответ приходит только от человека."""
    if not helper_ready():
        return {"granted": False, "writable": False, "error": i18n.t("rec.helper_missing")}
    return _ask(["calendar-request"], {}, timeout=180) or {"granted": False, "writable": False}


def calendars() -> list[dict]:
    """Календари, куда можно завести событие."""
    rows = _ask(["calendars"], [])
    return [r for r in (rows or []) if r.get("writable")]


def system_events(days: int = HORIZON, fresh: bool = False) -> list[dict]:
    """События из системного календаря — со всех подключённых аккаунтов."""
    key = f"events-{days}"
    hit = _seen.get(key)
    if hit and not fresh and time.time() - hit[0] < FRESH:
        return hit[1]
    rows = _ask(["calendar", str(days)], []) or []
    out = []
    for row in rows:
        start = float(row.get("start") or 0)
        end = float(row.get("end") or 0)
        if not start:
            continue
        out.append({
            "id": "sys:" + str(row.get("id") or start),
            "title": str(row.get("title") or "").strip() or i18n.t("agenda.untitled"),
            "start": start,
            "end": end or start + 1800,
            "allday": bool(row.get("allday")),
            "calendar": str(row.get("calendar") or ""),
            "account": str(row.get("account") or ""),
            "where": str(row.get("where") or ""),
            "url": str(row.get("url") or ""),
            "people": [str(x) for x in (row.get("people") or []) if x],
            "source": "system",
        })
    _seen[key] = (time.time(), out)
    return out


def _ask(command: list[str], fallback, timeout: int = 30):
    if not helper_ready():
        return fallback
    try:
        out = subprocess.run([str(HELPER), *command], capture_output=True,
                             text=True, timeout=timeout)
        return json.loads(out.stdout.strip() or "null") or fallback
    except Exception as exc:
        _log(f"календарь: {command[0]} — {exc!r}")
        return fallback


def _log(line: str) -> None:
    from . import record

    record.log(line)


# --- свои события ------------------------------------------------------------

def load() -> dict:
    try:
        data = json.loads(STORE.read_text("utf-8"))
    except Exception:
        return {"items": [], "fired": {}, "remind": {}}
    if not isinstance(data, dict):
        return {"items": [], "fired": {}, "remind": {}}
    data.setdefault("items", [])
    data.setdefault("fired", {})
    data.setdefault("remind", {})
    return data


def save(data: dict) -> None:
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def add(title: str, start: float, minutes: int = 30, where: str = "",
        url: str = "", people: list | None = None) -> dict:
    """Заводит свой созвон — тот, который ещё не дошёл до календаря."""
    item = {
        "id": "own:" + uuid.uuid4().hex[:10],
        "title": re.sub(r"\s+", " ", str(title or "")).strip()[:120] or i18n.t("agenda.untitled"),
        "start": float(start),
        "end": float(start) + max(5, int(minutes)) * 60,
        "allday": False,
        "calendar": "", "account": "", "where": str(where or "")[:200],
        "url": str(url or "")[:400],
        "people": [str(x)[:60] for x in (people or [])][:30],
        "source": "own",
    }
    data = load()
    data["items"].append(item)
    save(data)
    return item


def remove(item_id: str) -> bool:
    data = load()
    before = len(data["items"])
    data["items"] = [x for x in data["items"] if x.get("id") != item_id]
    data["fired"].pop(item_id, None)
    data.get("remind", {}).pop(item_id, None)
    save(data)
    return len(data["items"]) != before


def own_events() -> list[dict]:
    """Свои события, кроме давно прошедших."""
    edge = time.time() - 12 * 3600
    return [x for x in load().get("items", []) if float(x.get("end") or 0) >= edge]


def to_calendar(item_id: str, calendar: str = "") -> dict:
    """Отправляет своё событие в настоящий календарь — чтобы увидели остальные."""
    item = next((x for x in own_events() if x.get("id") == item_id), None)
    if item is None:
        return {"ok": False, "error": i18n.t("agenda.gone")}
    payload = json.dumps({
        "title": item["title"], "start": item["start"], "end": item["end"],
        "where": item.get("where", ""), "url": item.get("url", ""),
        "notes": i18n.t("agenda.fromApp"), "calendar": calendar,
    }, ensure_ascii=False)
    answer = _ask(["calendar-add", payload], {"ok": False}, timeout=60)
    if answer.get("ok"):
        remove(item_id)
        _seen.clear()
    return answer


# --- общий список ------------------------------------------------------------

def agenda(days: int = HORIZON, fresh: bool = False) -> dict:
    """Все созвоны в одном списке, с пометкой пересечений."""
    rows = system_events(days, fresh) + own_events()
    edge = time.time() + days * 86400
    rows = [r for r in rows if float(r.get("start") or 0) <= edge]
    rows.sort(key=lambda r: float(r.get("start") or 0))
    own = load().get("remind", {})
    for row in rows:
        row["call"] = is_call(row)
        row["day"] = _day_name(float(row["start"]))
        row["remind"] = list(own.get(row["id"]) or [])
    mark_overlaps(rows)
    return {"items": rows, **status()}


def is_call(row: dict) -> bool:
    """Похоже ли событие на созвон.

    Не всякая встреча — созвон: в календаре живут дни рождения, напоминания и
    «забрать посылку». Признак — ссылка на видеосвязь, а если её нет, то живые
    участники и не весь день.
    """
    if row.get("allday"):
        return False
    haystack = " ".join(str(row.get(k) or "") for k in ("url", "where", "notes", "title")).lower()
    if any(mark in haystack for mark in CALL_MARKS):
        return True
    return len(row.get("people") or []) >= 2


def mark_overlaps(rows: list[dict]) -> list[dict]:
    """Помечает события, которые накладываются друг на друга.

    Ради этого всё и затевалось: договариваясь о времени на созвоне, человек не
    помнит, что на этот час у него уже что-то стоит в другом календаре.
    """
    for row in rows:
        row["clash"] = []
    for i, one in enumerate(rows):
        if one.get("allday"):
            continue
        for two in rows[i + 1:]:
            if two.get("allday"):
                continue
            if float(two["start"]) >= float(one["end"]):
                break                      # список отсортирован — дальше не пересечёмся
            if float(two["start"]) < float(one["end"]) and float(one["start"]) < float(two["end"]):
                one["clash"].append(two["id"])
                two["clash"].append(one["id"])
    return rows


def free_at(start: float, minutes: int = 30) -> list[dict]:
    """Что уже стоит на это время — для ответа «а давай в три» прямо на созвоне."""
    end = float(start) + max(5, int(minutes)) * 60
    busy = []
    for row in agenda().get("items", []):
        if row.get("allday"):
            continue
        if float(row["start"]) < end and start < float(row["end"]):
            busy.append(row)
    return busy


# --- напоминания -------------------------------------------------------------

# Что предлагаем в готовом виде. Свои значения человек дописывает сам, но
# список из этих закрывает почти всё: сутки — чтобы успеть подготовиться,
# ноль — «началось».
COMMON = (1440, 120, 60, 30, 15, 5, 0)


def parse_reminders(value) -> list[int]:
    """Разбирает «60, 15, 0» в список минут. Мусор молча выбрасывается."""
    if isinstance(value, (list, tuple)):
        parts = [str(x) for x in value]
    else:
        parts = re.split(r"[,;\s]+", str(value or ""))
    out: list[int] = []
    for part in parts:
        part = part.strip()
        if not part.lstrip("-").isdigit():
            continue
        minutes = int(part)
        if 0 <= minutes <= 20160 and minutes not in out:   # не дальше двух недель
            out.append(minutes)
    return sorted(out, reverse=True)


def reminders_for(item_id: str, default) -> list[int]:
    """Свои интервалы у события сильнее общих."""
    own = load().get("remind", {}).get(item_id)
    if own:
        return parse_reminders(own)
    return parse_reminders(default)


def set_reminders(item_id: str, minutes) -> list[int]:
    """Задаёт интервалы конкретному созвону. Пусто — вернуться к общим."""
    data = load()
    remind = data.setdefault("remind", {})
    values = parse_reminders(minutes)
    if values:
        remind[item_id] = values
    else:
        remind.pop(item_id, None)
    # Отметки о сработавших сбрасываем: человек поменял правила, значит
    # напомнить по новым нужно даже о том, о чём уже напоминали.
    data.get("fired", {}).pop(item_id, None)
    save(data)
    return values


STALE = 300      # позже пяти минут после начала напоминать уже незачем


def due(default="30, 0", calls_only: bool = True) -> list[dict]:
    """О чём пора напомнить.

    У каждого события свой набор интервалов: общий из настроек или личный.
    Каждый интервал срабатывает один раз — отметка хранится по паре «событие
    + интервал», поэтому «за час» и «за пять минут» не мешают друг другу.

    Если приложение было закрыто и разом наступило несколько отметок, человек
    получает одно напоминание — по ближайшей к текущему моменту; остальные
    просроченные гасим молча, иначе на открытии прилетит очередь одинаковых.
    """
    now = time.time()
    data = load()
    fired = data.get("fired", {})
    rows = agenda().get("items", [])
    out = []
    for row in rows:
        if row.get("allday") or (calls_only and not row.get("call")):
            continue
        left = float(row["start"]) - now
        marks = list(fired.get(row["id"]) or [])
        wanted = reminders_for(row["id"], default)
        pending = [m for m in wanted if str(m) not in marks]
        ready = [m for m in pending if left <= m * 60]
        if not ready:
            continue
        marks.extend(str(m) for m in ready)
        fired[row["id"]] = marks
        if left < -STALE:                       # созвон давно начался — молчим
            continue
        minutes = min(ready)
        out.append({**row, "when": "now" if minutes == 0 else "soon",
                    "minutes": minutes})
    alive = {r["id"] for r in rows}
    data["fired"] = {k: v for k, v in fired.items() if k in alive}
    save(data)
    return out


# --- следующий созвон из разговора -------------------------------------------

TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.\s]([0-5]\d)(?!\d)")


def suggest(summary: dict, recorded_at: str = "", lang: str = "") -> dict | None:
    """Ищет в саммари договорённость о следующем созвоне.

    Только предлагает: ставить встречу в календарь по догадке модели нельзя.
    Дату берём из текста — «во вторник», «завтра» превращает в число тот же
    разбор, что уже расставляет сроки задачам. Без времени предложения нет:
    «созвонимся на следующей неделе» — это не встреча, а намерение.
    """
    sections = (summary or {}).get("sections") or {}
    base = dates.parse_stamp(recorded_at) or date.today()
    words = ("созвон", "созвонимся", "встреч", "встретимся", "call", "meet", "sync")
    for key in ("decisions", "tasks", "open", "summary"):
        for raw in str(sections.get(key) or "").split("\n"):
            line = re.sub(r"[|*_`#>]+", " ", raw).strip(" -•\t")
            if len(line) < 8:
                continue
            low = line.lower()
            if not any(w in low for w in words):
                continue
            clock = TIME_RE.search(line)
            if not clock:
                continue
            day = dates._day_for(low, base)
            if day is None:
                continue
            hour, minute = int(clock.group(1)), int(clock.group(2))
            when = datetime(day.year, day.month, day.day, hour, minute)
            if when.timestamp() < time.time():
                continue
            return {"title": line[:100], "start": when.timestamp(),
                    "when": when.strftime("%Y-%m-%d %H:%M"),
                    "busy": [b["title"] for b in free_at(when.timestamp())]}
    return None


# --- мелочи ------------------------------------------------------------------

def _day_name(stamp: float) -> str:
    day = datetime.fromtimestamp(stamp).date()
    today = date.today()
    if day == today:
        return i18n.t("agenda.today")
    if day == today + timedelta(days=1):
        return i18n.t("agenda.tomorrow")
    return dates.human(day, i18n.current())
