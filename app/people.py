"""Справочник: кто с кем работает.

Список участников созвона каждый раз набирается заново, хотя созваниваетесь вы
с одними и теми же людьми — и почти всегда командой: «подрядчик по стройке»,
«наш продукт», «заказчик». Поэтому здесь хранятся люди и их принадлежность к
организации или команде, а перед разговором список подставляется целиком, и
поправить его дешевле, чем набрать.

Голосовые отпечатки живут отдельно, в `voices.py`: у человека может не быть
запомненного голоса, а голос без имени — не человек. Связь между ними — имя, и
этого достаточно: справочник показывает, чей голос уже запомнен, а разбор
подставляет имя из памяти голосов.
"""

from __future__ import annotations

import json

from .settings import ROOT

STORE = ROOT / "people.json"
LIMIT = 400          # больше сотен людей в этом справочнике не бывает


def load() -> list[dict]:
    if not STORE.exists():
        return []
    try:
        data = json.loads(STORE.read_text("utf-8"))
    except Exception:
        return []
    found = []
    for item in (data.get("people") or [])[:LIMIT]:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        found.append({"name": name,
                      "org": str(item.get("org") or "").strip(),
                      "role": str(item.get("role") or "").strip()})
    return found


def save(people: list[dict]) -> None:
    STORE.write_text(json.dumps({"version": 1, "people": people[:LIMIT]},
                                ensure_ascii=False, indent=2), "utf-8")


def items(with_voices: bool = True) -> list[dict]:
    """Справочник для окна: люди по алфавиту, с отметкой о запомненном голосе."""
    known: set[str] = set()
    if with_voices:
        try:
            from . import voices

            known = set(voices.load())
        except Exception:
            known = set()
    people = sorted(load(), key=lambda p: (p["org"].lower(), p["name"].lower()))
    return [{**person, "voice": person["name"] in known} for person in people]


def add(name: str, org: str = "", role: str = "") -> dict:
    """Заводит человека или уточняет уже заведённого."""
    name = str(name or "").strip()[:60]
    if not name:
        return {"ok": False}
    org, role = str(org or "").strip()[:60], str(role or "").strip()[:60]
    people = load()
    for person in people:
        if person["name"].lower() == name.lower():
            # Организацию и роль дополняем, а не стираем пустым полем.
            person["org"] = org or person["org"]
            person["role"] = role or person["role"]
            save(people)
            return {"ok": True, "updated": True}
    people.append({"name": name, "org": org, "role": role})
    save(people)
    return {"ok": True, "added": True}


def remove(name: str) -> bool:
    people = load()
    left = [p for p in people if p["name"].lower() != str(name or "").strip().lower()]
    if len(left) == len(people):
        return False
    save(left)
    return True


def orgs() -> list[dict]:
    """Команды и компании со списком людей — из них собирается «кто на связи»."""
    groups: dict[str, list[str]] = {}
    for person in items():
        groups.setdefault(person["org"], []).append(person["name"])
    # Люди без организации идут последними: они не команда, а просто знакомые.
    return [{"org": org, "people": names}
            for org, names in sorted(groups.items(), key=lambda kv: (kv[0] == "", kv[0]))]


def of_org(org: str) -> list[str]:
    org = str(org or "").strip().lower()
    return [p["name"] for p in items() if p["org"].lower() == org]


def describe(name: str) -> str:
    """«Ирина · Подрядчик» — подпись для окна, если организация известна."""
    for person in load():
        if person["name"].lower() == str(name or "").strip().lower():
            bits = [person["name"]]
            if person["org"]:
                bits.append(person["org"])
            return " · ".join(bits)
    return str(name or "")
