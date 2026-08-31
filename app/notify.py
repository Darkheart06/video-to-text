"""Напоминания: в окно, в системную плашку, в Telegram и в MAX.

**Это единственное место, откуда что-то уходит с машины.** Всё остальное
приложение обещает работать без сети, и обещание держит. Здесь оно нарушается
осознанно и только по явному согласию: мессенджеры выключены по умолчанию, в
настройках написано, что именно уходит, а уходит ровно то, что человек выбрал —
время и название, при желании участники и итоги разбора. Никаких расшифровок,
никакого звука.

Запросы только исходящие. Единственное исключение — разовое чтение обновлений,
когда человек нажимает «Определить», чтобы приложение само нашло его чат: иначе
идентификатор чата пришлось бы добывать руками.

Системная плашка macOS честно ненадёжна: `display notification` показывается от
имени Script Editor, и если у того выключены уведомления, сообщение исчезает
молча — узнать об этом изнутри нельзя. Поэтому плашка в самом окне работает
всегда, а системная и мессенджеры идут дополнением; в настройках есть кнопка
«Проверить», чтобы человек увидел своими глазами, что у него работает.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import i18n

TELEGRAM = "https://api.telegram.org"
# Официальный адрес Bot API мессенджера MAX. Токен идёт заголовком:
# передача через параметр запроса там больше не поддерживается.
MAX_API = "https://platform-api2.max.ru"

TIMEOUT = 15


def send(settings, text: str, kind: str = "reminder") -> dict:
    """Рассылает сообщение всюду, где человек это разрешил."""
    out = {"banner": False, "telegram": False, "max": False}
    if settings.get("notify_banner", True):
        out["banner"] = banner(i18n.t("notify.title"), text)
    if settings.get("telegram_enabled") and kind_allowed(settings, kind):
        out["telegram"] = telegram(settings, text).get("ok", False)
    if settings.get("max_enabled") and kind_allowed(settings, kind):
        out["max"] = to_max(settings, text).get("ok", False)
    return out


def kind_allowed(settings, kind: str) -> bool:
    if kind == "summary":
        return bool(settings.get("notify_summary", True))
    return True


# --- системная плашка --------------------------------------------------------

def banner(title: str, text: str) -> bool:
    """Плашка macOS. Возвращает «отправили», а не «человек увидел»."""
    try:
        script = (f'display notification {_applescript(text)} '
                  f'with title {_applescript(title)}')
        done = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=10)
        return done.returncode == 0
    except Exception:
        return False


def _applescript(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"')[:400] + '"'


# --- Telegram ----------------------------------------------------------------

def telegram(settings, text: str) -> dict:
    token = str(settings.get("telegram_token") or "").strip()
    chat = str(settings.get("telegram_chat") or "").strip()
    if not token or not chat:
        return {"ok": False, "error": i18n.t("notify.needSetup")}
    body = urllib.parse.urlencode({
        "chat_id": chat, "text": text, "disable_web_page_preview": "true",
    }).encode()
    return _post(f"{TELEGRAM}/bot{token}/sendMessage", body,
                 {"Content-Type": "application/x-www-form-urlencoded"})


def telegram_chat(settings) -> dict:
    """Находит чат сам: человек пишет боту любое слово, мы читаем обновления.

    Иначе идентификатор чата пришлось бы добывать через сторонних ботов — самая
    частая причина, по которой такие настройки не доводят до конца.
    """
    token = str(settings.get("telegram_token") or "").strip()
    if not token:
        return {"ok": False, "error": i18n.t("notify.needToken")}
    answer = _get(f"{TELEGRAM}/bot{token}/getUpdates?limit=20&timeout=0")
    if not answer.get("ok"):
        return answer
    chats = []
    for update in reversed(answer.get("data", {}).get("result") or []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id") and chat["id"] not in [c["id"] for c in chats]:
            chats.append({"id": chat["id"],
                          "name": chat.get("title") or chat.get("first_name") or ""})
    if not chats:
        return {"ok": False, "error": i18n.t("notify.writeFirst")}
    return {"ok": True, "chat": str(chats[0]["id"]), "name": chats[0]["name"]}


# --- MAX ---------------------------------------------------------------------

def to_max(settings, text: str) -> dict:
    token = str(settings.get("max_token") or "").strip()
    chat = str(settings.get("max_chat") or "").strip()
    if not token or not chat:
        return {"ok": False, "error": i18n.t("notify.needSetup")}
    # Идентификатор чата и пользователя — разные параметры; человек мог
    # вписать любой, поэтому выбираем по виду: у пользователя он без минуса.
    where = "user_id" if chat.lstrip("-").isdigit() and not chat.startswith("-") else "chat_id"
    body = json.dumps({"text": text}).encode()
    return _post(f"{MAX_API}/messages?{where}={urllib.parse.quote(chat)}", body,
                 {"Content-Type": "application/json", "Authorization": token})


def max_chat(settings) -> dict:
    """То же, что и для Telegram: человек пишет боту, мы читаем обновления."""
    token = str(settings.get("max_token") or "").strip()
    if not token:
        return {"ok": False, "error": i18n.t("notify.needToken")}
    answer = _get(f"{MAX_API}/updates?limit=20", {"Authorization": token})
    if not answer.get("ok"):
        return answer
    for update in reversed(answer.get("data", {}).get("updates") or []):
        message = update.get("message") or {}
        recipient = message.get("recipient") or {}
        sender = (message.get("sender") or {})
        found = recipient.get("chat_id") or sender.get("user_id")
        if found:
            return {"ok": True, "chat": str(found),
                    "name": sender.get("name") or ""}
    return {"ok": False, "error": i18n.t("notify.writeFirst")}


# --- сеть --------------------------------------------------------------------

def _post(url: str, body: bytes, headers: dict) -> dict:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    return _run(request)


def _get(url: str, headers: dict | None = None) -> dict:
    return _run(urllib.request.Request(url, headers=headers or {}))


def _run(request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            raw = answer.read().decode("utf-8", "replace")
        return {"ok": True, "data": json.loads(raw or "{}")}
    except urllib.error.HTTPError as exc:
        # Текст ошибки от сервиса полезнее нашего: там написано, что не так с
        # токеном или чатом. Показываем его человеку как есть.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return {"ok": False, "error": f"{exc.code}: {detail or exc.reason}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
