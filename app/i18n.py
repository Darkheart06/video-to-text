"""Язык окна и язык документов.

Приложение говорит на двух языках. Разделены они намеренно: окно может быть
английским, а саммари — русским, потому что это разные вещи. Язык окна — про
того, кто нажимает кнопки; язык документа — про того, кто будет читать бриф,
и про язык самого разговора.

Строки собраны здесь, а не разложены по модулям: так видно, что перевод полон,
и не приходится искать по коду забытую фразу. Ключ — короткое имя вида
«stage.audio», значение — обе версии.

Подстановки — обычный format: `t("asr.part", n=2, total=6)`.
"""

from __future__ import annotations

import os
import platform
import subprocess

LANGUAGES = ("en", "ru")
FALLBACK = "en"

_current = FALLBACK
_system: str | None = None


def system_language() -> str:
    """Язык системы — «ru» или «en».

    На маке язык интерфейса живёт не в переменных окружения, а в настройках,
    поэтому сначала спрашиваем систему и только потом смотрим на LANG.
    """
    global _system
    if _system is not None:
        return _system
    value = ""
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                                 capture_output=True, text=True, timeout=3)
            value = out.stdout or ""
        except Exception:
            value = ""
    if not value:
        value = os.environ.get("LANG") or os.environ.get("LC_ALL") or ""
    _system = "ru" if "ru" in value.lower()[:40] else FALLBACK
    return _system


def pick(value: str, fallback: str = "") -> str:
    """Разворачивает «auto» в настоящий язык."""
    value = (value or "").strip().lower()
    if value in LANGUAGES:
        return value
    if value in ("", "auto", "system"):
        return (fallback if fallback in LANGUAGES else "") or system_language()
    return fallback if fallback in LANGUAGES else FALLBACK


def use(value: str) -> str:
    """Ставит язык окна на всё приложение — вызывается один раз при запуске."""
    global _current
    _current = pick(value)
    return _current


def current() -> str:
    return _current


def t(key: str, lang: str = "", **kw) -> str:
    """Строка на нужном языке. Без языка — на языке окна."""
    row = MESSAGES.get(key)
    if row is None:
        return key
    text = row.get(pick(lang, _current)) or row.get(FALLBACK) or key
    if kw:
        try:
            return text.format(**kw)
        except Exception:
            return text
    return text


# --- строки -----------------------------------------------------------------

MESSAGES: dict[str, dict[str, str]] = {
    # Этапы обработки: их видно в окне и в консоли.
    "stage.check": {"ru": "Проверка файла", "en": "Checking the file"},
    "stage.audio": {"ru": "Извлечение звуковой дорожки", "en": "Extracting the audio"},
    "stage.save": {"ru": "Сохранение результатов", "en": "Saving the results"},
    "state.done": {"ru": "Готово", "en": "Done"},
    "state.cancelled": {"ru": "Отменено", "en": "Cancelled"},
    "state.error": {"ru": "Ошибка", "en": "Error"},

    "asr.run": {"ru": "Распознавание речи", "en": "Recognising speech"},
    "asr.part": {"ru": "Распознавание, часть {n} из {total}",
                 "en": "Recognising, part {n} of {total}"},
    "asr.done": {"ru": "Распознавание завершено", "en": "Recognition finished"},

    "diar.run": {"ru": "Разделение по спикерам", "en": "Separating the voices"},
    "diar.compare": {"ru": "Сверяю голоса между собой",
                     "en": "Comparing the voices with each other"},
    "diar.limit": {"ru": "Голоса этой записи: порог {value}",
                   "en": "This recording's voices: threshold {value}"},
    "diar.found": {"ru": "Найдено спикеров: {n}", "en": "Speakers found: {n}"},
    "diar.after": {"ru": "Голосов после сверки: {n}", "en": "Voices after merging: {n}"},
    "diar.download_seg": {"ru": "Скачивание модели сегментации",
                          "en": "Downloading the segmentation model"},
    "diar.download_emb": {"ru": "Скачивание модели голосовых отпечатков",
                          "en": "Downloading the voice-print model"},
    "diar.models_ready": {"ru": "Модели готовы", "en": "Models ready"},

    "summary.making": {"ru": "{who} готовит {what}", "en": "{who} is writing {what}"},
    "summary.part": {"ru": "Разбор части {n} из {total}",
                     "en": "Working through part {n} of {total}"},
    "summary.assemble": {"ru": "Сборка итогового документа · {who}",
                         "en": "Assembling the document · {who}"},
    "summary.check": {"ru": "Проверяю, не упущено ли важное · {who}",
                      "en": "Checking what might be missing · {who}"},

    # Предупреждения: работа продолжается, но человек должен знать.
    "warn.no_speech": {"ru": "Речь не распознана — возможно, в записи нет голоса.",
                       "en": "No speech recognised — the recording may have no voice."},
    "warn.diar_failed": {"ru": "Разделение по спикерам не выполнено: {error}",
                         "en": "Speaker separation failed: {error}"},
    "warn.diar_skip": {"ru": "Продолжаю без разделения по спикерам",
                       "en": "Continuing without speaker separation"},
    "warn.voices_failed": {"ru": "Знакомые голоса не проверены: {error}",
                           "en": "Could not check for familiar voices: {error}"},
    "warn.summary_failed": {"ru": "Саммари не составлено: {error}",
                            "en": "No summary was made: {error}"},
    "warn.summary_skip": {"ru": "Продолжаю без саммари", "en": "Continuing without a summary"},

    # Ошибки со звуком и файлами.
    "err.ffmpeg": {"ru": "ffmpeg не найден. Установите его: brew install ffmpeg",
                   "en": "ffmpeg not found. Install it: brew install ffmpeg"},
    "err.no_audio": {"ru": "В файле нет звуковой дорожки — распознавать нечего.",
                     "en": "The file has no audio track — nothing to recognise."},
    "err.not_found": {"ru": "Файл не найден: {path}", "en": "File not found: {path}"},
    "err.format": {"ru": "Формат {ext} не поддерживается. Поддерживаются: {list}",
                   "en": "Format {ext} is not supported. Supported: {list}"},
    "err.read": {"ru": "Не удалось прочитать файл: {error}",
                 "en": "Could not read the file: {error}"},
    "err.extract": {"ru": "Не удалось извлечь звук: {error}",
                    "en": "Could not extract the audio: {error}"},
    "err.wav": {"ru": "Ожидался моно WAV 16 кГц", "en": "Expected mono 16 kHz WAV"},
    "err.asr_missing": {
        "ru": "Не найден движок распознавания. Установите mlx-whisper или faster-whisper.",
        "en": "No recognition engine found. Install mlx-whisper or faster-whisper.",
    },
    "err.sherpa": {
        "ru": "Не установлен sherpa-onnx — разделение по спикерам недоступно.",
        "en": "sherpa-onnx is not installed — speaker separation is unavailable.",
    },
    "err.seg_archive": {
        "ru": "Архив с моделью сегментации распакован не так, как ожидалось",
        "en": "The segmentation model archive unpacked differently than expected",
    },
    "err.sample_rate": {"ru": "Модель ждёт {model} Гц, а аудио {audio} Гц",
                        "en": "The model expects {model} Hz, the audio is {audio} Hz"},

    # Языковая модель.
    "llm.no_answer": {"ru": "Модель не ответила", "en": "The model gave no answer"},
    "llm.file": {"ru": "Файл · {name}", "en": "File · {name}"},
    "llm.nothing_worked": {"ru": "Ни один способ подключить модель не сработал.\n",
                           "en": "None of the ways to reach a model worked.\n"},
    "llm.ping_rules": {"ru": "Отвечай одним словом.", "en": "Answer with one word."},
    "llm.ping": {"ru": "Ответь словом «готово», если ты меня понимаешь.",
                 "en": "Reply with the word “ready” if you understand me."},
    "llm.hello": {"ru": "привет", "en": "hello"},
    "llm.ollama_empty": {
        "ru": "В Ollama нет ни одной модели. Установите: ollama pull qwen3:8b",
        "en": "Ollama has no models installed. Install one: ollama pull qwen3:8b",
    },
    "llm.ollama_down": {
        "ru": "Ollama не отвечает по адресу {url}. Запустите приложение Ollama "
              "или команду `ollama serve`. ({error})",
        "en": "Ollama is not answering at {url}. Start the Ollama app "
              "or run `ollama serve`. ({error})",
    },
    "llm.ollama_error": {"ru": "Ollama ответила ошибкой {code}",
                         "en": "Ollama replied with error {code}"},
    "llm.ollama_timeout": {"ru": "Ollama перестала отвечать: {error}",
                           "en": "Ollama stopped responding: {error}"},
    "llm.model_missing": {
        "ru": "Модель «{name}» не установлена. Есть: {have}. Установить: ollama pull {name}",
        "en": "Model “{name}” is not installed. Available: {have}. "
              "Install it: ollama pull {name}",
    },
    "llm.gguf_unset": {
        "ru": "Не указан файл модели. Настройки → «Файл модели (.gguf)» → «Выбрать файл…».",
        "en": "No model file selected. Settings → “Model file (.gguf)” → “Choose a file…”.",
    },
    "llm.gguf_missing": {"ru": "Файл модели не найден: {path}",
                         "en": "Model file not found: {path}"},
    "llm.gguf_ext": {"ru": "Ожидался файл .gguf, а выбран {what}",
                     "en": "Expected a .gguf file, got {what}"},
    "llm.gguf_noext": {"ru": "файл без расширения", "en": "a file with no extension"},
    "llm.gguf_lib": {
        "ru": "Не установлена библиотека llama-cpp-python, без неё файл .gguf не открыть. "
              "Установите: .venv/bin/pip install llama-cpp-python",
        "en": "llama-cpp-python is not installed, and a .gguf file cannot be opened "
              "without it. Install it: .venv/bin/pip install llama-cpp-python",
    },
    "llm.gguf_load": {
        "ru": "Не удалось загрузить {name}. Часто помогает уменьшить размер контекста "
              "в настройках.",
        "en": "Could not load {name}. Reducing the context size in settings often helps.",
    },
    "llm.server_empty": {"ru": "Сервер {url} не отдал ни одной модели.",
                         "en": "The server at {url} returned no models."},
    "llm.server_down": {
        "ru": "Сервер модели не отвечает по адресу {url}. Проверьте, что он запущен "
              "и что адрес заканчивается на /v1. ({error})",
        "en": "The model server is not answering at {url}. Check that it is running "
              "and that the address ends with /v1. ({error})",
    },
    "llm.server_error": {"ru": "Сервер ответил ошибкой {code}",
                         "en": "The server replied with error {code}"},
    "llm.server_timeout": {"ru": "Сервер модели перестал отвечать: {error}",
                           "en": "The model server stopped responding: {error}"},
    "llm.failed": {"ru": "Модель не смогла ответить: {error}",
                   "en": "The model could not answer: {error}"},

    # Архив и правка.
    "lib.from_archive": {"ru": "Из архива", "en": "From the archive"},
    "lib.in_text": {"ru": "в тексте", "en": "in the text"},
    "lib.missing": {"ru": "Запись не найдена", "en": "Recording not found"},
    "attach.missing": {"ru": "Документ не найден", "en": "Document not found"},
    "app.files_docs": {"ru": "Документы (*.pdf;*.docx;*.pptx;*.xlsx;*.txt;*.md;*.csv;*.rtf)",
                       "en": "Documents (*.pdf;*.docx;*.pptx;*.xlsx;*.txt;*.md;*.csv;*.rtf)"},
    "lib.gone": {"ru": "Файлы уже удалены", "en": "The files are already gone"},
    "edit.no_summary": {"ru": "У этой записи нет саммари",
                        "en": "This recording has no summary"},
    "edit.no_section": {"ru": "Такого раздела в записи нет",
                        "en": "There is no such section in this recording"},
    "edit.unreadable": {"ru": "Не удалось прочитать запись: {error}",
                        "en": "Could not read the recording: {error}"},

    # Запись созвона и встречи.
    "rec.screen_denied": {
        "ru": "macOS не разрешила записывать экран, а без этого не слышно собеседников. "
              "Откройте «Конфиденциальность и безопасность» → «Запись экрана» и разрешите "
              "приложению доступ.",
        "en": "macOS did not allow screen recording, and without it the other side is "
              "inaudible. Open Privacy & Security → Screen Recording and allow the app.",
    },
    "rec.capture_failed": {"ru": "Захват звука не запустился",
                           "en": "Audio capture did not start"},
    "rec.helper_missing": {"ru": "Помощник захвата не установлен",
                           "en": "The capture helper is not installed"},
    "rec.helper_reinstall": {
        "ru": "Помощник захвата не установлен — переустановите приложение.",
        "en": "The capture helper is not installed — reinstall the app.",
    },
    "rec.already": {"ru": "Запись уже идёт", "en": "A recording is already running"},
    "rec.cancelled": {"ru": "Запись отменена", "en": "Recording cancelled"},
    "rec.running_room": {"ru": "Идёт запись встречи", "en": "Recording the meeting"},
    "rec.running_call": {"ru": "Идёт запись", "en": "Recording"},
    "rec.finishing": {"ru": "Завершаю запись", "en": "Finishing the recording"},
    "rec.assembling": {"ru": "Собираю запись", "en": "Putting the recording together"},
    "rec.queued": {"ru": "Ждёт разбора", "en": "Waiting to be processed"},
    "rec.paused": {"ru": "Разбор на паузе — идёт новая запись",
                   "en": "Processing paused — a new recording is running"},
    "rec.resumed": {"ru": "Продолжаю разбор", "en": "Back to processing"},
    "rec.empty": {"ru": "Записать звук не удалось — дорожки пустые",
                  "en": "Nothing was captured — both tracks are empty"},
    "rec.counted": {"ru": "Записано {minutes} мин, реплик {lines}",
                    "en": "{minutes} min recorded, {lines} lines"},
    "rec.title_room": {"ru": "Встреча ", "en": "Meeting "},
    "rec.title_call": {"ru": "Созвон ", "en": "Call "},
    "rec.what_room": {"ru": "запись встречи", "en": "meeting recording"},
    "rec.what_call": {"ru": "запись созвона", "en": "call recording"},
    "rec.naming": {"ru": "Придумываю название", "en": "Coming up with a title"},
    "rec.summarising": {"ru": "Готовлю саммари и бриф — {seconds} с",
                        "en": "Writing the summary and brief — {seconds} s"},
    "rec.splitting_room": {"ru": "Разбираю, кто говорил на встрече — {seconds} с",
                           "en": "Working out who spoke at the meeting — {seconds} s"},
    "rec.splitting_call": {"ru": "Разбираю, кто из собеседников говорил — {seconds} с",
                           "en": "Working out who said what on the call — {seconds} s"},
    "rec.voices_room": {"ru": "Голосов на встрече: {n}", "en": "Voices at the meeting: {n}"},
    "rec.voices_call": {"ru": "Собеседников на звонке: {n}", "en": "People on the call: {n}"},
    "rec.split_failed": {"ru": "Голоса не разобраны: {error}",
                         "en": "Voices were not separated: {error}"},
    "rec.split_failed_call": {"ru": "Голоса собеседников не разобраны: {error}",
                              "en": "The other side's voices were not separated: {error}"},
    "rec.recognised": {"ru": "Узнал по голосу: {names}", "en": "Recognised by voice: {names}"},
    "rec.mic_silent": {
        "ru": "Микрофон молчит — пишу и разбираю только собеседников",
        "en": "The microphone is silent — recording and transcribing the other side only",
    },
    "rec.sys_silent": {
        "ru": "Системный звук молчит — пишу и разбираю только ваш микрофон",
        "en": "System audio is silent — recording and transcribing your microphone only",
    },

    # Окно.
    "app.no_pywebview": {
        "ru": "Не установлен pywebview — окно не открыть.\n"
              "Установите: pip install pywebview\nИли пользуйтесь консольной версией.",
        "en": "pywebview is not installed — the window cannot open.\n"
              "Install it: pip install pywebview\nOr use the command-line version.",
    },
    "app.files_media": {"ru": "Видео и аудио", "en": "Video and audio"},
    "app.files_all": {"ru": "Все файлы (*.*)", "en": "All files (*.*)"},
    "app.files_gguf": {"ru": "Файл модели (*.gguf)", "en": "Model file (*.gguf)"},
    "app.no_recording": {"ru": "Файл записи не найден", "en": "Recording file not found"},
    "app.title": {"ru": "Расшифровка записей", "en": "Transcripts"},
}


# --- слова, которые попадают в сами документы -------------------------------

DOCUMENT: dict[str, dict[str, str]] = {
    "speaker": {"ru": "Спикер {n}", "en": "Speaker {n}"},
    "speaker_prefix": {"ru": "Спикер ", "en": "Speaker "},
    "unknown": {"ru": "Неизвестный", "en": "Unknown"},
    "me": {"ru": "Я", "en": "Me"},
    "them": {"ru": "Собеседник", "en": "Them"},
    "them_numbered": {"ru": "Собеседник {n}", "en": "Person {n}"},
    "call": {"ru": "созвон", "en": "call"},
    "recording": {"ru": "запись", "en": "recording"},

    "transcript_title": {"ru": "# Транскрипт — {title}", "en": "# Transcript — {title}"},
    "summary_title": {"ru": "# Саммари и бриф — {title}",
                      "en": "# Summary and brief — {title}"},
    "meta.source": {"ru": "Источник", "en": "Source"},
    "meta.duration": {"ru": "Длительность", "en": "Duration"},
    "meta.language": {"ru": "Язык", "en": "Language"},
    "meta.speakers": {"ru": "Спикеров", "en": "Speakers"},
    "meta.processed": {"ru": "Обработано", "en": "Processed"},
    "meta.models": {"ru": "Модели", "en": "Models"},

    "out.dir": {"ru": "Расшифровка записей", "en": "Transcripts"},
    "out.tables": {"ru": ".таблицы.csv", "en": ".tables.csv"},
}


def d(key: str, lang: str, **kw) -> str:
    """Слово для документа — язык здесь всегда указывается явно."""
    row = DOCUMENT.get(key)
    if row is None:
        return key
    text = row.get(pick(lang, FALLBACK)) or row.get(FALLBACK) or key
    if kw:
        try:
            return text.format(**kw)
        except Exception:
            return text
    return text


def out_dir_names() -> tuple[str, ...]:
    """Все возможные имена папки с результатами — архиву нужны обе."""
    return tuple(dict.fromkeys(DOCUMENT["out.dir"][lang] for lang in LANGUAGES))


def table_suffixes() -> tuple[str, ...]:
    return tuple(dict.fromkeys(DOCUMENT["out.tables"][lang] for lang in LANGUAGES))
