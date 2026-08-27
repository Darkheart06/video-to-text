"""Подключение языковой модели. Три способа на выбор:

1. ``ollama``  — приложение Ollama на этом же компьютере (проще всего);
2. ``gguf``    — файл модели .gguf на диске, без всякого сервера;
3. ``openai``  — любой сервер с OpenAI-совместимым API: LM Studio, llama-server,
                 Jan, LocalAI, а при желании и облачный сервис с ключом.

Все три отдают одинаковый интерфейс: ``backend.chat(system, prompt) -> str``.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import i18n

BACKENDS = ("ollama", "gguf", "openai")

# Что предпочесть, когда в Ollama несколько моделей, а в настройках «auto»
OLLAMA_PREFERENCE = [
    "gemma4:12b-mlx", "gemma4:12b", "gemma4:e4b-mlx", "gemma4:e4b",
    "qwen3:14b", "qwen3:8b", "qwen3:4b",
    "gemma3:12b", "gemma3:4b",
    "llama3.1:8b", "mistral-nemo:latest", "qwen2.5:7b",
]

TEMPERATURE = 0.2
# Сколько модель может написать в ответ. Замер на 51-минутном созвоне: и
# gemma4, и qwen3.5 упирались в 3000 и обрывали подробности, поэтому по
# умолчанию вдвое больше. Меняется настройкой llm_max_tokens.
MAX_TOKENS = 6000

# Сколько держать модель в памяти между запросами. Без этого Ollama выгружает
# её через пять минут, и следующий запрос снова ждёт загрузки нескольких
# гигабайт — на записи созвона это самая заметная задержка.
KEEP_ALIVE = "30m"

# Рассуждающие модели (gemma4, qwen3 и им подобные) иногда пишут ход мыслей
# прямо в ответ. В бриф это попадать не должно.
_THINK_RE = re.compile(
    r"<(think|thinking|reasoning)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)


def clean_answer(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    # Незакрытый блок рассуждений: всё до закрывающего тега — мусор.
    tail = re.split(r"</(?:think|thinking|reasoning)>", text, maxsplit=1)
    if len(tail) == 2:
        text = tail[1]
    return text.strip()


class LLMError(RuntimeError):
    """Модель недоступна или ответила ошибкой. Текст рассчитан на показ человеку."""


# --- вспомогательное ---------------------------------------------------------

def _get_json(url: str, timeout: int = 10, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: int, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_ollama_models(url: str, timeout: int = 10) -> list[str]:
    url = (url or "http://127.0.0.1:11434").rstrip("/")
    try:
        data = _get_json(f"{url}/api/tags", timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        raise LLMError(i18n.t("llm.ollama_down", url=url, error=exc)) from exc
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def list_openai_models(base_url: str, headers: dict | None = None,
                       timeout: int = 10) -> list[str]:
    base = (base_url or "http://127.0.0.1:1234/v1").rstrip("/")
    try:
        data = _get_json(f"{base}/models", timeout=timeout, headers=headers)
    except (urllib.error.URLError, OSError) as exc:
        raise LLMError(i18n.t("llm.server_down", url=base, error=exc)) from exc
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


# --- 1. Ollama ---------------------------------------------------------------

class OllamaBackend:
    kind = "ollama"

    def __init__(self, url: str, model: str, num_ctx: int, timeout: int = 900,
                 max_tokens: int = MAX_TOKENS) -> None:
        self.url = (url or "http://127.0.0.1:11434").rstrip("/")
        self.num_ctx = int(num_ctx)
        self.max_tokens = int(max_tokens or MAX_TOKENS)
        self.timeout = timeout
        self.model = self._pick(model)
        self.name = f"Ollama · {self.model}"

    def list_models(self) -> list[str]:
        return list_ollama_models(self.url)

    def _pick(self, preferred: str) -> str:
        installed = self.list_models()
        if not installed:
            raise LLMError(i18n.t("llm.ollama_empty"))
        if preferred and preferred != "auto":
            if preferred in installed:
                return preferred
            base = preferred.split(":")[0]
            for name in installed:
                if name.split(":")[0] == base:
                    return name
            raise LLMError(i18n.t("llm.model_missing", name=preferred,
                                  have=", ".join(installed)))
        for want in OLLAMA_PREFERENCE:
            if want in installed:
                return want
            base = want.split(":")[0]
            for name in installed:
                if name.split(":")[0] == base:
                    return name
        return installed[0]

    def _payload(self, system: str, prompt: str, think: bool | None) -> dict:
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {
                "temperature": TEMPERATURE,
                "num_ctx": self.num_ctx,
                "num_predict": self.max_tokens,
            },
        }
        if think is not None:
            body["think"] = think
        return body

    def chat(self, system: str, prompt: str) -> str:
        # Рассуждающие модели вроде gemma4 тратят на «подумать» больше времени,
        # чем на сам ответ, а в бриф эти размышления всё равно не попадают.
        # Просим не рассуждать; если модель такого не умеет — повторяем как есть.
        for think in (False, None):
            try:
                data = _post_json(f"{self.url}/api/chat",
                                  self._payload(system, prompt, think),
                                  timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if think is False:
                    continue
                raise LLMError(i18n.t("llm.ollama_error", code=exc.code)) from exc
            except (urllib.error.URLError, OSError) as exc:
                raise LLMError(i18n.t("llm.ollama_timeout", error=exc)) from exc
            if "error" in data:
                if think is False:
                    continue
                raise LLMError(str(data["error"]))
            return clean_answer((data.get("message", {}) or {}).get("content", ""))
        raise LLMError(i18n.t("llm.no_answer"))

    def warm(self) -> None:
        """Заранее поднимает модель в память — чтобы к концу созвона она уже
        была готова и саммари не ждало загрузки."""
        try:
            _post_json(f"{self.url}/api/chat", {
                "model": self.model, "stream": False, "keep_alive": KEEP_ALIVE,
                "messages": [{"role": "user", "content": i18n.t("llm.hello")}],
                "options": {"num_predict": 1, "num_ctx": self.num_ctx},
                "think": False,
            }, timeout=600)
        except Exception:
            pass


# --- 2. Файл .gguf напрямую --------------------------------------------------

_GGUF_CACHE: dict[tuple, object] = {}


class GgufBackend:
    """Модель загружается прямо из файла .gguf через llama-cpp-python.

    Сервер не нужен: указали путь к файлу — и всё. Экземпляр модели кэшируется,
    иначе каждая запись заново читала бы с диска несколько гигабайт.
    """

    kind = "gguf"

    def __init__(self, path: str, num_ctx: int, threads: int = 0,
                 gpu_layers: int = -1, max_tokens: int = MAX_TOKENS) -> None:
        self.max_tokens = int(max_tokens or MAX_TOKENS)
        if not path:
            raise LLMError(i18n.t("llm.gguf_unset"))
        model_path = Path(path).expanduser()
        if not model_path.exists():
            raise LLMError(i18n.t("llm.gguf_missing", path=model_path))
        if model_path.suffix.lower() != ".gguf":
            raise LLMError(i18n.t("llm.gguf_ext",
                                  what=model_path.suffix or i18n.t("llm.gguf_noext")))

        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise LLMError(i18n.t("llm.gguf_lib")) from exc

        self.path = model_path
        self.name = i18n.t("llm.file", name=model_path.name)
        key = (str(model_path), int(num_ctx), int(gpu_layers))
        model = _GGUF_CACHE.get(key)
        if model is None:
            try:
                model = Llama(
                    model_path=str(model_path),
                    n_ctx=int(num_ctx),
                    n_threads=int(threads) or None,
                    n_gpu_layers=int(gpu_layers),
                    verbose=False,
                )
            except Exception as exc:
                raise LLMError(f"{i18n.t('llm.gguf_load', name=model_path.name)} ({exc})"
                               ) from exc
            _GGUF_CACHE.clear()      # держим в памяти только одну модель
            _GGUF_CACHE[key] = model
        self.model = model

    def warm(self) -> None:
        return None

    def chat(self, system: str, prompt: str) -> str:
        try:
            out = self.model.create_chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=TEMPERATURE,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            raise LLMError(i18n.t("llm.failed", error=exc)) from exc
        return clean_answer(out["choices"][0]["message"]["content"])


# --- 3. OpenAI-совместимый сервер --------------------------------------------

class OpenAIBackend:
    """LM Studio, llama-server, Jan, LocalAI и любой другой /v1/chat/completions."""

    kind = "openai"

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout: int = 900, max_tokens: int = MAX_TOKENS) -> None:
        self.max_tokens = int(max_tokens or MAX_TOKENS)
        self.base = (base_url or "http://127.0.0.1:1234/v1").rstrip("/")
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model = model.strip() if model and model != "auto" else self._first_model()
        self.name = f"API · {self.model}"

    def list_models(self) -> list[str]:
        return list_openai_models(self.base, self.headers)

    def _first_model(self) -> str:
        models = self.list_models()
        if not models:
            raise LLMError(i18n.t("llm.server_empty", url=self.base))
        return models[0]

    def warm(self) -> None:
        return None

    def chat(self, system: str, prompt: str) -> str:
        try:
            data = _post_json(f"{self.base}/chat/completions", {
                "model": self.model,
                "temperature": TEMPERATURE,
                "max_tokens": self.max_tokens,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }, timeout=self.timeout, headers=self.headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"{i18n.t('llm.server_error', code=exc.code)}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LLMError(i18n.t("llm.server_timeout", error=exc)) from exc
        if "error" in data:
            raise LLMError(str(data["error"]))
        return clean_answer(data["choices"][0]["message"]["content"])


# --- выбор бэкенда -----------------------------------------------------------

def build(settings) -> OllamaBackend | GgufBackend | OpenAIBackend:
    """Собирает бэкенд по настройкам. При «auto» пробует по очереди."""
    choice = settings.get("llm_backend", "auto")
    ctx = int(settings.get("llm_num_ctx", 32768))
    limit = int(settings.get("llm_max_tokens", MAX_TOKENS) or MAX_TOKENS)

    def gguf():
        return GgufBackend(settings.get("gguf_path", ""), ctx,
                           threads=int(settings.get("num_threads", 0)),
                           gpu_layers=int(settings.get("gguf_gpu_layers", -1)),
                           max_tokens=limit)

    def ollama():
        return OllamaBackend(settings.get("ollama_url", ""),
                             settings.get("ollama_model", "auto"), ctx,
                             max_tokens=limit)

    def openai():
        return OpenAIBackend(settings.get("openai_base_url", ""),
                             settings.get("openai_model", "auto"),
                             settings.get("openai_api_key", ""),
                             max_tokens=limit)

    if choice == "gguf":
        return gguf()
    if choice == "ollama":
        return ollama()
    if choice == "openai":
        return openai()

    problems = []
    for name, make in (("gguf", gguf), ("ollama", ollama), ("openai", openai)):
        if name == "gguf" and not settings.get("gguf_path"):
            continue
        try:
            return make()
        except LLMError as exc:
            problems.append(f"{name}: {exc}")
    raise LLMError(i18n.t("llm.nothing_worked") + "\n".join(problems))


def probe(settings) -> dict:
    """Короткая сводка для интерфейса: что доступно прямо сейчас."""
    import importlib.util

    result: dict = {"llama_cpp": importlib.util.find_spec("llama_cpp") is not None}

    try:
        result["ollama_models"] = list_ollama_models(settings.get("ollama_url", ""), timeout=4)
        result["ollama_error"] = ""
    except LLMError as exc:
        result["ollama_models"], result["ollama_error"] = [], str(exc)

    key = settings.get("openai_api_key", "")
    try:
        result["openai_models"] = list_openai_models(
            settings.get("openai_base_url", ""),
            {"Authorization": f"Bearer {key}"} if key else None,
            timeout=4,
        )
        result["openai_error"] = ""
    except LLMError as exc:
        result["openai_models"], result["openai_error"] = [], str(exc)

    path = settings.get("gguf_path", "")
    file = Path(path).expanduser() if path else None
    result["gguf_path_ok"] = bool(file and file.exists())
    result["gguf_size_gb"] = (
        round(file.stat().st_size / 2 ** 30, 1) if result["gguf_path_ok"] else 0
    )
    return result


def self_test(settings) -> dict:
    """Кнопка «Проверить связь» в настройках: реально дёргает модель."""
    try:
        backend = build(settings)
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        answer = backend.chat(i18n.t("llm.ping_rules"), i18n.t("llm.ping"))
    except LLMError as exc:
        return {"ok": False, "error": str(exc), "backend": backend.name}
    return {"ok": True, "backend": backend.name, "answer": answer[:120]}


# Небольшая подсказка для тех, кто ставит llama-cpp-python вручную
INSTALL_LLAMA_CPP = (
    "CMAKE_ARGS='-DGGML_METAL=on' "
    f"{os.path.join('.venv', 'bin', 'pip')} install llama-cpp-python"
)
