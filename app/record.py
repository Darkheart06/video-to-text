"""Режим стенографиста: запись созвона с расшифровкой по ходу разговора.

Звук берётся двумя дорожками — свой микрофон и то, что звучит из динамиков.
Это даёт точное «я / собеседники» без догадок по голосовым отпечаткам:
кто где говорил, известно из самого источника записи.

Пока идёт разговор, накопленный звук небольшими кусками уходит в Whisper,
а раз в несколько минут модель делает короткую сводку. По завершении
собирается полный транскрипт, саммари и бриф — как для обычного файла.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

import numpy as np

from . import asr, cleanup, diarize, i18n, llm, media, merge, presets, render, summarize, voices
from .settings import WORK_DIR, Settings

SAMPLE_RATE = media.SAMPLE_RATE
BYTES_PER_SECOND = SAMPLE_RATE * 2

# Насколько одна дорожка может отстать от другой, прежде чем мы перестанем её
# ждать. Десять секунд — заметно больше любой буферизации и заметно меньше
# того, что человек готов ждать, глядя на пустой транскрипт.
LAG_LIMIT = 10

# Помощник живёт внутри «Расшифровка.app»: разрешение на запись экрана macOS
# выдаёт программе, и когда помощник лежит в бандле, в списке разрешений
# появляется само приложение, а не тот, кто его запустил. Путь подсказывает
# launcher; вне бандла — запасной вариант рядом с проектом.
HELPER = Path(os.environ.get("V2T_HELPER") or (media.BUNDLED_BIN / "v2t-capture"))

Listener = Callable[[str, dict], None]


class RecordError(RuntimeError):
    pass


# macOS сообщает об отказе в записи экрана кодом -3801 и формулировкой,
# по которой невозможно догадаться, что делать. Переводим на человеческий.
DENIED = ("-3801", "SCStreamErrorDomain Code=-3801", "declined")


def friendly_error(text: str) -> str:
    if any(mark in text for mark in DENIED):
        return i18n.t("rec.screen_denied")
    return text.strip()[-300:] or i18n.t("rec.capture_failed")


@dataclass
class Line:
    start: float
    end: float
    who: str          # "me" | "them"
    text: str
    raw: str = ""     # как было сказано, до чистки
    # После звонка системная дорожка разбирается по голосам, и здесь
    # появляется «Собеседник 2» вместо общего «Собеседник».
    speaker: str = ""
    # Какой голос узнан по ходу записи: «V1», «V2»… Имя может смениться, голос
    # остаётся — переименовав одну реплику, человек переименовывает весь голос.
    voice: str = ""
    # Подпись поставил человек прямо во время записи — такую не перебиваем
    # догадками диаризации, наоборот, по ней и узнаём голос.
    tagged: bool = False

    @property
    def label(self) -> str:
        if self.speaker:
            return self.speaker
        if self.who == "me":
            return i18n.d("me", i18n.current())
        # На встрече в комнате все голоса идут в один микрофон, и пока запись
        # не разобрана, честнее не подписывать реплику вовсе, чем врать.
        return i18n.d("them", i18n.current()) if self.who == "them" else ""


@dataclass
class Session:
    id: str
    started_at: float
    directory: Path
    title: str
    lines: list[Line] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    state: str = "recording"      # recording | finishing | done | error
    message: str = ""
    error: str = ""
    files: dict = field(default_factory=dict)
    summary_sections: dict = field(default_factory=dict)
    summary_tabs: list = field(default_factory=list)
    summary_md: str = ""
    preset: str = ""
    mode: str = "call"       # call — созвон двумя дорожками, room — встреча в комнате
    people: list[str] = field(default_factory=list)   # кого отмечаем по ходу
    # Голоса, узнанные по ходу: ключ -> несколько отпечатков (сравниваем с
    # лучшим из них, а не со средним: усреднение смазывает короткие реплики).
    voices: dict = field(default_factory=dict)
    voice_names: dict = field(default_factory=dict)   # «V2» -> «Спикер 2» или имя
    # Голоса с настоящим именем: их не сводят между собой и не перенумеровывают.
    named: set = field(default_factory=set)
    stamp: str = ""          # «2026-08-27 13-32» — дата и время начала
    renamed: bool = False    # название уже подобрано по теме разговора
    stalled: str = ""        # какая дорожка встала: «mic», «sys» или никакая

    @property
    def duration(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def snapshot(self, tail: int = 400) -> dict:
        return {
            "id": self.id, "state": self.state, "title": self.title,
            "duration": round(self.duration, 1), "message": self.message,
            "error": self.error, "files": self.files,
            "notes": self.notes,
            "summary_md": self.summary_md,
            "summary_sections": self.summary_sections,
            "summary_tabs": self.summary_tabs,
            "preset": self.preset,
            "mode": self.mode,
            "people": list(self.people),
            # Голоса, которые приложение уже различило по ходу разговора:
            # человеку остаётся поправить имя, а не расставлять всё с нуля.
            "voices": [{"key": key, "name": self.voice_names.get(key, key),
                        "lines": sum(1 for line in self.lines if line.voice == key)}
                       for key in sorted(self.voices)],
            "lines": [
                {"start": round(line.start, 1), "who": line.who,
                 "label": line.label, "text": line.text,
                 "tagged": line.tagged, "voice": line.voice, "index": i}
                for i, line in enumerate(self.lines)
            ][-tail:],
            "line_count": len(self.lines),
        }


# --- разрешения и наличие помощника ------------------------------------------

def helper_ready() -> bool:
    return HELPER.exists()


def permissions() -> dict:
    """Что macOS уже разрешила: запись экрана (звук системы) и микрофон."""
    if not helper_ready():
        return {"screen": False, "microphone": False,
                "error": i18n.t("rec.helper_missing")}
    try:
        out = subprocess.run([str(HELPER), "check"], capture_output=True,
                             text=True, timeout=10)
        return json.loads(out.stdout.strip() or "{}")
    except Exception as exc:
        return {"screen": False, "microphone": False, "error": str(exc)}


def request_permissions() -> dict:
    """Показывает системные запросы. Запись экрана macOS даёт только после
    перезапуска программы — об этом сообщаем отдельно."""
    if not helper_ready():
        return {"screen": False, "microphone": False,
                "error": i18n.t("rec.helper_missing")}
    try:
        out = subprocess.run([str(HELPER), "request"], capture_output=True,
                             text=True, timeout=120)
        return json.loads(out.stdout.strip() or "{}")
    except Exception as exc:
        return {"screen": False, "microphone": False, "error": str(exc)}


def mic_busy() -> bool:
    """Занят ли микрофон кем-то ещё — верный признак идущего созвона."""
    if not helper_ready():
        return False
    try:
        out = subprocess.run([str(HELPER), "mic-status"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() == "1"
    except Exception:
        return False


# --- работа с сырыми дорожками -----------------------------------------------

def _read_tail(path: Path, offset: int) -> bytes:
    if not path.exists():
        return b""
    size = path.stat().st_size
    if size <= offset:
        return b""
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(size - offset)


def _to_float(raw: bytes) -> np.ndarray:
    if not raw:
        return np.zeros(0, dtype=np.float32)
    usable = len(raw) - (len(raw) % 2)
    return np.frombuffer(raw[:usable], dtype=np.int16).astype(np.float32) / 32768.0


def assign_room(lines: list[Line], spans: list,
                lang: str = "") -> tuple[dict[int, str], dict[str, str]]:
    """Раскладывает реплики встречи по найденным голосам.

    Здесь нет «своей» дорожки: все, включая меня, говорят в один микрофон,
    поэтому спикеры получаются такие же безымянные, как при разборе файла, —
    пока их не назовут по имени.
    """
    keys = dict.fromkeys(range(len(lines)), "S1")
    lang = i18n.pick(lang, i18n.current())
    order: dict[int, int] = {}
    for i, line in enumerate(lines):
        found = merge._speaker_at(spans, line.start, line.end)
        if found is None:
            continue
        if found not in order:
            order[found] = len(order)
        keys[i] = f"S{order[found] + 1}"
    count = max(1, len(order))
    names = {f"S{n + 1}": i18n.d("speaker", lang, n=n + 1) for n in range(count)}
    return keys, names


def assign_others(lines: list[Line], spans: list,
                  lang: str = "") -> tuple[dict[int, str], dict[str, str]]:
    """Раскладывает реплики собеседников по найденным голосам.

    Своя дорожка не участвует: «Я» и так известен. Номера даём по первому
    появлению в разговоре, чтобы «Собеседник 1» был тем, кто заговорил раньше.
    """
    lang = i18n.pick(lang, i18n.current())
    keys = {i: ("S1" if line.who == "me" else "S2") for i, line in enumerate(lines)}
    names = {"S1": i18n.d("me", lang), "S2": i18n.d("them", lang)}
    if len({s.speaker for s in spans}) < 2:
        return keys, names

    order: dict[int, int] = {}
    for i, line in enumerate(lines):
        if line.who != "them":
            continue
        found = merge._speaker_at(spans, line.start, line.end)
        if found is None:
            continue
        if found not in order:
            order[found] = len(order)
        keys[i] = f"S{order[found] + 2}"

    if len(order) < 2:
        return ({i: ("S1" if line.who == "me" else "S2")
                 for i, line in enumerate(lines)}, names)
    names = {"S1": i18n.d("me", lang)}
    for number in range(len(order)):
        names[f"S{number + 2}"] = i18n.d("them_numbered", lang, n=number + 1)
    return keys, names


# Сколько отпечатков помним на голос: больше — устойчивее узнавание, но и
# дольше сравнение. Восьми хватает, чтобы захватить и спокойную речь, и смех.
VOICE_PRINTS = 8


def _print_of(extractor, piece: np.ndarray):
    """Голосовой отпечаток одного куска речи, готовый к сравнению."""
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=piece)
    stream.input_finished()
    vector = np.array(extractor.compute(stream), dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else None


def _write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(audio, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((data * 32767).astype(np.int16).tobytes())


# Слова-паразиты только мешают сравнивать два распознавания одной и той же
# фразы: в одной дорожке Whisper их слышит, в другой нет.
_FILLER = {"вот", "ну", "это", "эт", "как", "бы", "то", "есть", "да", "а", "и",
           "так", "там", "уже", "щас", "сейчас", "получается", "короче",
           "the", "a", "an", "so", "well", "like", "just", "you", "know",
           "i", "mean", "uh", "um", "yeah", "okay", "ok", "right"}


def normalize(text: str) -> str:
    words = re.findall(r"[\w-]+", text.lower().replace("ё", "е"))
    kept = [w for w in words if w not in _FILLER]
    return " ".join(kept or words)


def similar(a: str, b: str) -> float:
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _loudness(audio: np.ndarray, start: float, end: float, base: float) -> float:
    """Громкость дорожки на заданном отрезке — по ней решаем, где голос настоящий."""
    a = max(0, int((start - base) * SAMPLE_RATE))
    b = min(audio.size, int((end - base) * SAMPLE_RATE))
    if b - a < SAMPLE_RATE // 20:
        return 0.0
    return float(np.sqrt(np.mean(audio[a:b] ** 2)))


def _has_speech(audio: np.ndarray, floor: float = 0.006) -> bool:
    """Отсекает тишину до Whisper: на пустом звуке он охотно выдумывает фразы."""
    if audio.size < SAMPLE_RATE // 4:
        return False
    return float(np.sqrt(np.mean(audio ** 2))) > floor


def drop_echo(lines: list[Line], mic: np.ndarray, spk: np.ndarray,
              base: float, threshold: float = 0.6) -> list[Line]:
    """Убирает одну и ту же фразу, попавшую в обе дорожки.

    Так бывает всегда: свой голос macOS подмешивает в системный звук, а речь
    собеседника из динамиков возвращается в микрофон. Оставляем ту дорожку,
    где голос громче — она и есть настоящий источник, вторая лишь эхо.
    """
    mine = [line for line in lines if line.who == "me"]
    theirs = [line for line in lines if line.who == "them"]
    if not mine or not theirs:
        return lines

    drop: set[int] = set()
    for a in mine:
        for b in theirs:
            if id(a) in drop or id(b) in drop:
                continue
            # Одна фраза не может звучать в разное время
            if min(a.end, b.end) - max(a.start, b.start) <= 0 and \
                    abs(a.start - b.start) > 2.0:
                continue
            if similar(a.text, b.text) < threshold:
                continue
            start, end = min(a.start, b.start), max(a.end, b.end)
            loud_mic = _loudness(mic, start, end, base)
            loud_spk = _loudness(spk, start, end, base)
            drop.add(id(b) if loud_mic >= loud_spk else id(a))

    return [line for line in lines if id(line) not in drop]


# --- сам стенографист --------------------------------------------------------

class Stenographer:
    """Одна запись созвона от кнопки «Начать» до готового брифа."""

    def __init__(self, settings: Settings, listener: Listener | None = None) -> None:
        self.settings = settings
        self.listener = listener
        self.session: Session | None = None
        self._process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._voice_model = None
        self._voice_library: dict | None = None
        # Разбор записи идёт отдельно от самой записи: разговор не ждёт, а
        # разбор — подождёт. Иначе новый созвон, начавшийся сразу после
        # предыдущего, приходится пропускать, пока считается прошлый.
        self.queue: list[Session] = []
        self._busy: Session | None = None
        self._processor: threading.Thread | None = None
        self._wake = threading.Event()

    # --- события ---------------------------------------------------------

    def _emit(self, event: str = "record", session: Session | None = None) -> None:
        # Окно показывает одну запись — ту, что человек ведёт сейчас. Разбор
        # прошлой идёт фоном, и его сообщения приходят внутри той же карточки,
        # строкой очереди: иначе окно перескакивало бы на чужую запись.
        shown = self.session or session
        if session is not None and self.session is not None \
                and session is not self.session and self.session.state != "recording":
            shown = session
        if self.listener and shown:
            try:
                self.listener(event, self.snapshot_of(shown))
            except Exception:
                pass

    def _say(self, message: str, session: Session | None = None) -> None:
        target = session or self.session
        if target:
            target.message = message
            self._emit(session=target)

    def snapshot_of(self, session: Session) -> dict:
        """Снимок сессии вместе с очередью разбора — окно показывает обе."""
        data = session.snapshot()
        data["queue"] = [{"id": item.id, "title": item.title,
                          "message": item.message, "state": item.state}
                         for item in ([self._busy] if self._busy else []) + list(self.queue)
                         if item is not session]
        return data

    # --- управление ------------------------------------------------------

    def is_active(self) -> bool:
        """Идёт ли запись прямо сейчас. Разбор прошлой записи этому не мешает:
        он ушёл в очередь и подождёт."""
        return self.session is not None and self.session.state == "recording"

    def busy_with(self) -> list[Session]:
        """Что сейчас разбирается и что ждёт очереди."""
        return ([self._busy] if self._busy else []) + list(self.queue)

    def start(self, title: str = "", preset: str = "", mode: str = "call") -> dict:
        with self._lock:
            if self.is_active():
                raise RecordError(i18n.t("rec.already"))
            if not helper_ready():
                raise RecordError(i18n.t("rec.helper_reinstall"))
            # Предварительную проверку разрешения не делаем: macOS отвечает на
            # неё неточно. Пробуем начать по-настоящему — отказ придёт от самой
            # системы, и уже понятной формулировкой.

            session_id = uuid.uuid4().hex[:10]
            directory = WORK_DIR / f"rec-{session_id}"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H-%M")
            room = mode == "room"
            self.session = Session(
                id=session_id, started_at=time.time(), directory=directory,
                mode="room" if room else "call",
                title=title.strip() or (i18n.t("rec.title_room" if room else
                                               "rec.title_call") + stamp),
                preset=preset or self.settings.get("preset", presets.DEFAULT),
                stamp=stamp,
            )
            self._stop.clear()
            self._process = subprocess.Popen(
                [str(HELPER), "mic" if room else "record", str(directory)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()
            self._say(i18n.t("rec.running_room" if room else "rec.running_call"))
            return self.session.snapshot()

    def stop(self) -> dict | None:
        with self._lock:
            if not self.session or self.session.state != "recording":
                return self.session.snapshot() if self.session else None
            self.session.state = "finishing"
            self._say(i18n.t("rec.finishing"))
            self._stop.set()
        if self._worker:
            self._worker.join(timeout=900)
        return self.session.snapshot() if self.session else None

    def cancel(self) -> None:
        """Прервать и всё выбросить — на случай «записал не то»."""
        with self._lock:
            if not self.session:
                return
            self._stop.set()
            self.session.state = "done"
            self.session.message = i18n.t("rec.cancelled")
        self._terminate_helper()
        if self.session:
            shutil.rmtree(self.session.directory, ignore_errors=True)
        self._emit()

    def _terminate_helper(self) -> None:
        proc = self._process
        if not proc or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # --- основной цикл ---------------------------------------------------

    def _run(self) -> None:
        session = self.session
        assert session is not None
        mic_path = session.directory / "mic.pcm"
        sys_path = session.directory / "sys.pcm"
        offset = 0                      # сколько байт каждой дорожки уже разобрано
        chunk_bytes = int(self.settings["record_chunk_seconds"]) * BYTES_PER_SECOND
        notes_every = int(self.settings["record_notes_minutes"]) * 60
        next_note = notes_every if notes_every else float("inf")

        # Пока идёт разговор, поднимаем модель в память: к концу созвона она
        # будет готова, и саммари не придётся ждать загрузки нескольких гигабайт.
        if self.settings["summary_enabled"]:
            threading.Thread(target=self._warm_model, daemon=True).start()

        try:
            # Помощник мог не подняться — например, отозвали разрешение.
            time.sleep(1.5)
            if self._process and self._process.poll() not in (None, 0):
                err = (self._process.stderr.read() or b"").decode("utf-8", "replace")
                raise RecordError(friendly_error(err))

            while not self._stop.is_set():
                ready = self._ready(mic_path, sys_path)
                if ready - offset >= chunk_bytes:
                    offset = self._consume(mic_path, sys_path, offset, ready)
                    if session.duration >= next_note:
                        self._make_note()
                        next_note += notes_every
                time.sleep(1.0)

            # Остановка: забираем хвост и собираем результат
            self._terminate_helper()
            time.sleep(0.6)
            ready = self._ready(mic_path, sys_path)
            if ready > offset:
                self._consume(mic_path, sys_path, offset, ready, final=True)
            self._enqueue(session)

        except Exception as exc:
            self._terminate_helper()
            session.state = "error"
            session.error = str(exc)
            session.message = i18n.t("state.error")
            self._emit()

    # --- очередь разбора --------------------------------------------------

    def _enqueue(self, session: Session) -> None:
        """Ставит запись в очередь на разбор и будит обработчик."""
        session.state = "queued"
        session.message = i18n.t("rec.queued")
        with self._lock:
            self.queue.append(session)
            if self._processor is None or not self._processor.is_alive():
                self._processor = threading.Thread(target=self._process_queue,
                                                   daemon=True)
                self._processor.start()
        self._wake.set()
        self._emit(session=session)

    def _process_queue(self) -> None:
        """Разбирает записи по одной, уступая дорогу живой записи."""
        while True:
            with self._lock:
                self._busy = self.queue.pop(0) if self.queue else None
                session = self._busy
            if session is None:
                self._wake.clear()
                if not self._wake.wait(30):
                    with self._lock:
                        if not self.queue:
                            self._processor = None
                            return
                continue
            try:
                self._finish(session)
            except Exception as exc:
                session.state = "error"
                session.error = str(exc)
                session.message = i18n.t("state.error")
                self._emit(session=session)
            finally:
                with self._lock:
                    self._busy = None
                self._emit()

    def _hold(self, session: Session) -> None:
        """Ждёт, пока идёт живая запись: она важнее разбора.

        Проверяется между этапами, а не внутри них: остановить Whisper на
        середине нельзя, но между «собрать звук», «разделить голоса» и
        «саммари» пауза безопасна и почти незаметна.
        """
        told = False
        while self.session is not None and self.session is not session \
                and self.session.state == "recording":
            if not told:
                self._say(i18n.t("rec.paused"), session)
                told = True
            time.sleep(2.0)
        if told:
            self._say(i18n.t("rec.resumed"), session)

    def _ready(self, mic_path: Path, sys_path: Path) -> int:
        """Сколько байт можно разбирать.

        Обычно ждём, пока обе дорожки допишутся до одного места, — иначе
        реплики разъедутся во времени. Но одна дорожка может встать совсем:
        не выдано разрешение на микрофон, выбрано не то устройство ввода, а на
        созвоне в наушниках человек и вовсе может весь час молчать. Ждать её —
        значит не расшифровывать разговор вообще, хотя собеседники слышны
        прекрасно. Поэтому если отставание перевалило за LAG_LIMIT, идём по
        той дорожке, которая живёт.
        """
        mic = mic_path.stat().st_size if mic_path.exists() else 0
        if self.session and self.session.mode == "room":
            return mic
        sys_size = sys_path.stat().st_size if sys_path.exists() else 0
        together, ahead = min(mic, sys_size), max(mic, sys_size)
        if ahead - together > LAG_LIMIT * BYTES_PER_SECOND:
            self._note_stalled(mic < sys_size)
            # Отступаем от самого края: последние доли секунды дописываются
            # прямо сейчас, и читать их — значит поймать полреплики.
            return max(together, ahead - BYTES_PER_SECOND)
        return together

    def _note_stalled(self, mic_stalled: bool) -> None:
        """Говорим об отставшей дорожке один раз, а не каждые полсекунды."""
        session = self.session
        if not session or session.stalled == ("mic" if mic_stalled else "sys"):
            return
        session.stalled = "mic" if mic_stalled else "sys"
        self._say(i18n.t("rec.mic_silent" if mic_stalled else "rec.sys_silent"))

    def _warm_model(self) -> None:
        try:
            llm.build(self.settings).warm()
        except Exception:
            pass

    def _consume(self, mic_path: Path, sys_path: Path, offset: int,
                 upto: int, final: bool = False) -> int:
        """Разбирает кусок обеих дорожек и добавляет реплики в транскрипт."""
        session = self.session
        assert session is not None
        length = upto - offset
        if length < BYTES_PER_SECOND:
            return offset

        room = session.mode == "room"
        mic = _to_float(_read_tail(mic_path, offset)[:length])
        spk = (np.zeros(0, dtype=np.float32) if room
               else _to_float(_read_tail(sys_path, offset)[:length]))
        base = offset / BYTES_PER_SECOND

        # Режем не по счётчику, а по тишине — иначе слово рвётся пополам.
        if not final:
            mixed = np.abs(mic[:len(spk)]) + np.abs(spk[:len(mic)]) \
                if len(mic) and len(spk) else np.abs(mic if len(mic) else spk)
            cut = _quiet_point(mixed)
            if cut > SAMPLE_RATE:
                mic, spk = mic[:cut], spk[:cut]
                length = cut * 2

        fresh: list[Line] = []
        tracks = ((mic, "room"),) if room else ((mic, "me"), (spk, "them"))
        for track, who in tracks:
            if not _has_speech(track):
                continue
            fresh.extend(self._transcribe(track, base, who))

        # Эхо бывает только там, где дорожки две.
        if not room and self.settings.get("record_dedupe", True):
            fresh = drop_echo(fresh, mic, spk, base)
        cleanup.clean_turns(fresh, bool(self.settings.get("transcript_cleanup", True)))
        self._live_voices(fresh, mic, spk, base, room)

        session.lines.extend(fresh)
        session.lines.sort(key=lambda item: item.start)
        self._say(i18n.t("rec.counted", minutes=int(session.duration // 60),
                         lines=len(session.lines)))
        return offset + length


    # --- голоса по ходу разговора -----------------------------------------

    def _extractor(self):
        """Модель голосовых отпечатков — одна на всю запись, грузится лениво."""
        if self._voice_model is None:
            self._voice_model = diarize.embedder(int(self.settings["num_threads"]))
        return self._voice_model

    def _live_voices(self, fresh: list[Line], mic: np.ndarray, spk: np.ndarray,
                     base: float, room: bool) -> None:
        """Раздаёт свежим репликам номера голосов прямо во время записи.

        Раньше номера появлялись только после остановки, и всю запись человек
        видел безликое «Собеседник». Теперь каждая реплика сразу сравнивается с
        уже услышанными голосами: похоже — тот же «Спикер 2», не похоже — новый.
        Разбор после остановки всё равно будет точнее (там видно запись
        целиком), но по ходу разговора важнее не точность, а то, что есть за
        что зацепиться: поправить имя проще, чем расставить его с нуля.
        """
        session = self.session
        if not session or not self.settings.get("live_speakers", True):
            return
        floor = float(self.settings.get("live_voice_floor", 0.5))
        limit = int(self.settings.get("live_voice_limit", 9))
        # Человек сказал, кто на связи, — значит, столько людей и есть. Одно
        # место про запас на того, кого он не назвал; больше голосов заводить
        # незачем: лишний чип не помогает, а сбивает с толку.
        if session.people:
            limit = min(limit, len(session.people) + 1)
        sticky = float(self.settings.get("live_voice_sticky", 0.08))
        gap = float(self.settings.get("live_voice_gap", 2.0))
        try:
            extractor = self._extractor()
        except Exception:
            return

        # Кто говорил последним и когда закончил — отсюда берётся фора для
        # продолжения фразы. Свежие реплики ещё не в session.lines.
        last_key, last_end = "", -1e9
        for earlier in reversed(session.lines):
            if earlier.voice:
                last_key, last_end = earlier.voice, earlier.end
                break

        for line in fresh:
            if line.tagged or (line.who == "me" and not room):
                continue                      # своя дорожка — это я, и так ясно
            audio = mic if (room or line.who == "me") else spk
            a = int(max(0.0, line.start - base) * SAMPLE_RATE)
            b = int(max(0.0, line.end - base) * SAMPLE_RATE)
            piece = audio[a:b]
            if piece.size < SAMPLE_RATE * 1.5:
                continue                      # на короткой реплике отпечаток врёт
            # Отпечаток тишины похож на отпечаток любой другой тишины, и стоит
            # такому «голосу» появиться первым, как к нему притягивается всё
            # подряд — мужчина и женщина оказываются одним человеком. Поэтому
            # берём только куски, где действительно есть речь, и порог здесь
            # выше, чем для Whisper: отпечатку нужен голос, а не шорох.
            if not _has_speech(piece, floor=0.012):
                continue
            try:
                print_ = _print_of(extractor, piece)
            except Exception:
                return
            if print_ is None:
                continue

            best, score = "", 0.0
            for key, prints in session.voices.items():
                near = max(float(print_ @ other) for other in prints)
                # Человек не меняется в середине фразы. Если предыдущий кусок
                # речи кончился только что, продолжает почти наверняка он же —
                # его голосу даётся фора, иначе одна фраза, разрезанная на
                # части, разъезжается по трём «спикерам».
                if key == last_key and line.start - last_end <= gap:
                    near += sticky
                if near > score:
                    best, score = key, near

            if score < floor and len(session.voices) < limit:
                best = f"V{len(session.voices) + 1}"
                session.voices[best] = [print_]
                # Голос могли запомнить на прошлых записях — тогда он сразу
                # приходит с именем, и подписывать заново не нужно.
                known, _ = voices.match(print_, people=self._known())
                session.voice_names[best] = known or self._voice_title(
                    len(session.voices), room)
                if known:
                    session.named.add(best)
                    if known not in session.people:
                        session.people.append(known)
            elif not best:
                continue
            else:
                prints = session.voices[best]
                prints.append(print_)
                # Держим несколько отпечатков, а не среднее: человек звучит
                # по-разному, когда говорит быстро, тихо или смеётся.
                del prints[:-VOICE_PRINTS]

            line.voice = best
            line.speaker = session.voice_names.get(best, "")
            last_key, last_end = best, line.end

        self._fold_voices()

    def _fold_voices(self) -> None:
        """Сводит живые голоса, которые оказались одним человеком.

        По одной короткой реплике голос узнать трудно, и человек разъезжается
        на «Собеседника 2» и «Собеседника 3». Но отпечатков со временем
        набирается больше, и то, что вначале было неочевидно, потом видно:
        такие голоса складываются в один, а реплики переподписываются.

        Сравнение — полной связью, как в разборе записи: две группы сходятся,
        только если *каждый* отпечаток одной похож на *каждый* отпечаток
        другой. По ближайшей паре голоса склеивались бы цепочкой.
        """
        session = self.session
        limit = float(self.settings.get("live_voice_fold", 0.72))
        if not session or len(session.voices) < 2:
            return
        keys = list(session.voices)
        into: dict[str, str] = {}

        # Два чипа с одним именем — это один человек, и сказал это не алгоритм,
        # а человек, который их назвал. Такие сводим независимо от похожести:
        # ровно так и написано в подсказке под голосами.
        seen: dict[str, str] = {}
        for key in keys:
            name = session.voice_names.get(key, "").strip().lower()
            if not name or key not in session.named:
                continue
            if name in seen:
                into[key] = seen[name]
            else:
                seen[name] = key

        if limit >= 1.0:
            self._apply_fold(into)
            return
        for i, first in enumerate(keys):
            if first in into:
                continue
            for second in keys[i + 1:]:
                if second in into:
                    continue
                # Названный человек — не кандидат на слияние: имя дал живой
                # человек, и молча переносить его на другой голос нельзя.
                if second in session.named or first in session.named:
                    continue
                pairs = [float(a @ b) for a in session.voices[first]
                         for b in session.voices[second]]
                if pairs and min(pairs) >= limit:
                    into[second] = first
        self._apply_fold(into)

    def _apply_fold(self, into: dict[str, str]) -> None:
        """Переносит отпечатки и реплики слитых голосов и наводит порядок."""
        session = self.session
        if not session or not into:
            return
        for second, first in into.items():
            session.voices[first] = (session.voices[first]
                                     + session.voices.pop(second))[-VOICE_PRINTS:]
            session.voice_names.pop(second, None)
            session.named.discard(second)
        for line in session.lines:
            if line.voice in into:
                line.voice = into[line.voice]
                # Подпись берём у того голоса, в который влились: человек мог
                # написать имя со строчной буквы или с лишним пробелом.
                line.speaker = session.voice_names.get(line.voice, line.speaker)
        self._renumber_voices()

    def _renumber_voices(self) -> None:
        """После слияния номера идут подряд: «Собеседник 1, 3» сбивает с толку."""
        session = self.session
        assert session is not None
        room = session.mode == "room"
        number = 0
        for key in session.voices:
            if key in session.named:
                continue
            number += 1
            session.voice_names[key] = self._voice_title(number, room)
        for line in session.lines:
            if line.voice and not line.tagged:
                line.speaker = session.voice_names.get(line.voice, line.speaker)

    def _known(self) -> dict:
        """Запомненные голоса — читаем один раз на запись, не на реплику."""
        if self._voice_library is None:
            self._voice_library = voices.load() if self.settings.get(
                "known_voices", True) else {}
        return self._voice_library

    def _voice_title(self, number: int, room: bool) -> str:
        """Как назвать только что услышанный голос."""
        lang = i18n.current()
        if room:
            return i18n.d("speaker", lang, n=number)
        return i18n.d("them_numbered", lang, n=number)

    def _transcribe(self, audio: np.ndarray, base: float, who: str) -> list[Line]:
        session = self.session
        assert session is not None
        temp = session.directory / f"chunk-{who}.wav"
        _write_wav(temp, audio)
        try:
            result = asr.transcribe(temp, self.settings)
        except Exception:
            return []
        finally:
            temp.unlink(missing_ok=True)
        return [
            Line(start=base + s.start, end=base + s.end, who=who, text=s.text.strip())
            for s in result.segments if s.text.strip()
        ]

    # --- заметки по ходу -------------------------------------------------

    def _make_note(self) -> None:
        session = self.session
        assert session is not None
        if not self.settings["summary_enabled"]:
            return
        seen = sum(len(n.get("covered", [])) for n in session.notes)
        fresh = session.lines[seen:]
        if len(fresh) < 4:
            return
        text = "\n".join(f"{line.label}: {line.text}" for line in fresh)
        try:
            backend = llm.build(self.settings)
            lang = self.settings.doc_lang
            answer = backend.chat(summarize.SYSTEM[lang],
                                  summarize.PROMPTS[lang]["live_notes"] + text)
        except Exception:
            return
        session.notes.append({
            "at": round(session.duration),
            "text": answer.strip(),
            "covered": list(range(seen, len(session.lines))),
        })
        self._emit()

    # --- разметка по ходу записи ------------------------------------------

    def set_people(self, names: list[str]) -> dict | None:
        """Список участников: кого можно отметить одним кликом."""
        session = self.session
        if not session:
            return None
        clean, seen = [], set()
        for name in names or []:
            value = str(name).strip()[:40]
            if value and value.lower() not in seen:
                seen.add(value.lower())
                clean.append(value)
        session.people = clean[:12]
        self._emit()
        return session.snapshot()

    def tag(self, index: int, name: str) -> dict | None:
        """Привязывает реплику к человеку прямо во время записи.

        Это не только подпись: по отмеченным репликам приложение потом узнаёт
        голос и подставит имя по всей записи — это надёжнее, чем разбирать
        безымянные кластеры и переименовывать их постфактум.
        """
        session = self.session
        if not session or not (0 <= index < len(session.lines)):
            return None
        value = str(name or "").strip()[:40]
        line = session.lines[index]
        if not value:
            # Снимаем имя, но не голос: реплика возвращается к «Спикеру 2»,
            # а не становится безымянной.
            line.tagged = False
            line.speaker = session.voice_names.get(line.voice, "") if line.voice else ""
            if line.voice:
                session.voice_names[line.voice] = self._voice_title(
                    int(line.voice[1:]) if line.voice[1:].isdigit() else 1,
                    session.mode == "room")
                for other in session.lines:
                    if other.voice == line.voice and not other.tagged:
                        other.speaker = session.voice_names[line.voice]
        else:
            line.speaker, line.tagged = value, True
            if value not in session.people:
                session.people.append(value)
            # Правим одну реплику, а не весь голос. Раньше имя расходилось на
            # весь голос сразу, и стоило приложению слепить двух человек в один
            # голос, как поправить отдельную реплику становилось нечем.
            # Целиком голос переименовывается кликом по чипу в строке голосов.
            owner = next((key for key, named in session.voice_names.items()
                          if named == value), "")
            if owner and owner != line.voice:
                # Этот человек уже узнан под другим голосом — переносим реплику
                # к нему: дальше она будет считаться его речью.
                line.voice = owner
        self._emit()
        return session.snapshot()

    def rename_voice(self, key: str, name: str) -> dict | None:
        """Даёт имя целому голосу — всем его репликам сразу.

        Приложение само пронумеровало голоса по ходу разговора, и человеку
        остаётся только сказать, кто есть кто: одно имя вместо десятка отметок.
        """
        session = self.session
        if not session or key not in session.voices:
            return None
        value = str(name or "").strip()[:40]
        if not value:
            number = int(key[1:]) if key[1:].isdigit() else 1
            value = self._voice_title(number, session.mode == "room")
            session.voice_names[key] = value
            session.named.discard(key)
            for line in session.lines:
                if line.voice == key:
                    line.speaker, line.tagged = value, False
        else:
            session.voice_names[key] = value
            session.named.add(key)
            if value not in session.people:
                session.people.append(value)
            for line in session.lines:
                if line.voice == key:
                    line.speaker, line.tagged = value, True
        # Назвали второй чип тем же именем — значит, это один человек, и держать
        # два голоса больше незачем.
        self._fold_voices()
        self._emit()
        return session.snapshot()

    def _enrolled(self) -> dict[str, list[tuple[float, float]]]:
        """Отмеченные вручную куски речи, по именам."""
        session = self.session
        assert session is not None
        out: dict[str, list[tuple[float, float]]] = {}
        for line in session.lines:
            if line.tagged and line.speaker and line.end - line.start >= 0.8:
                out.setdefault(line.speaker, []).append((line.start, line.end))
        return out

    def apply_names(self, audio: np.ndarray, keys: dict[int, str],
                    names: dict[str, str], only: str | None = None,
                    session: Session | None = None) -> dict[str, str]:
        """Подставляет имена, расставленные по ходу, всей записи целиком.

        Сравниваем отмеченные куски речи с **каждой репликой отдельно**, а не с
        усреднённым голосом кластера. Проверено на настоящей записи: кусок и
        средний отпечаток своего же кластера сошлись всего на 0.52, тогда как
        чужой голос дал 0.40 — по абсолютной величине не отличить. Значение
        имеет разрыв между лучшим и вторым, а не само число.

        Дальше голосуют реплики: имя достаётся кластеру, если за него набралось
        больше половины узнанного времени. Так одна ошибка на реплике не
        переименовывает всю запись.
        """
        session = session or self.session
        assert session is not None
        enrolled = self._enrolled()
        if not enrolled:
            return names
        floor = float(self.settings.get("voice_match_floor", 0.35))
        margin = float(self.settings.get("voice_match_margin", 0.07))

        try:
            extractor = diarize.embedder(int(self.settings["num_threads"]))

            def print_of(ranges: list[tuple[float, float]]):
                spans = [diarize.SpeakerSpan(a, b, 0) for a, b in ranges]
                return diarize.voice_print(extractor, audio, spans)

            people = {name: print_of(ranges) for name, ranges in enrolled.items()}
            people = {k: v for k, v in people.items() if v is not None}
            if not people:
                return names

            votes: dict[str, dict[str, float]] = {}
            weight: dict[str, float] = {}
            for i, line in enumerate(session.lines):
                key = keys.get(i)
                if not key or (only and line.who != only):
                    continue
                seconds = max(0.0, line.end - line.start)
                weight[key] = weight.get(key, 0.0) + seconds
                if line.tagged and line.speaker:
                    votes.setdefault(key, {})[line.speaker] = \
                        votes.setdefault(key, {}).get(line.speaker, 0.0) + seconds * 2
                    continue
                if seconds < 0.8:
                    continue
                voice = print_of([(line.start, line.end)])
                if voice is None:
                    continue
                scored = sorted(((float(vector @ voice), name)
                                 for name, vector in people.items()), reverse=True)
                best, name = scored[0]
                second = scored[1][0] if len(scored) > 1 else -1.0
                if best < floor or best - second < margin:
                    continue
                votes.setdefault(key, {})[name] = \
                    votes.setdefault(key, {}).get(name, 0.0) + seconds
        except Exception:
            return names
        if not votes:
            return names

        # Имя получает тот кластер, где за него больше всего узнанного времени;
        # одно имя — одному голосу.
        claims = sorted(
            ((seconds, name, key) for key, tally in votes.items()
             for name, seconds in tally.items()),
            reverse=True,
        )
        taken_names: set[str] = set()
        taken_keys: set[str] = set()
        fresh = dict(names)
        for seconds, name, key in claims:
            if name in taken_names or key in taken_keys:
                continue
            counted = sum(votes[key].values())
            if counted <= 0 or seconds < counted * 0.5:
                continue                       # голоса разделились — не гадаем
            taken_names.add(name)
            taken_keys.add(key)
            fresh[key] = name
        if taken_names:
            self._say(i18n.t("rec.recognised", names=", ".join(sorted(taken_names))))
        return fresh

    # --- кто говорил на встрече -------------------------------------------

    def split_room(self, audio: np.ndarray,
                   session: Session | None = None) -> tuple[dict[int, str], dict[str, str]]:
        """Разбирает запись встречи по голосам — так же, как обычный файл."""
        session = session or self.session
        assert session is not None
        keys = dict.fromkeys(range(len(session.lines)), "S1")
        names = {"S1": i18n.d("speaker", self.settings.doc_lang, n=1)}
        if not self.settings.get("record_split_speakers", True):
            return keys, names
        if len(session.lines) < 2 or audio.size < SAMPLE_RATE * 15:
            return keys, names

        path = session.directory / "room.wav"
        ticking = threading.Event()
        started = time.time()

        def tick() -> None:
            while not ticking.wait(3):
                self._say(i18n.t("rec.splitting_room",
                                 seconds=int(time.time() - started)))

        try:
            _write_wav(path, audio)
            threading.Thread(target=tick, daemon=True).start()
            spans = diarize.diarize(path, {
                **self.settings,
                "min_duration_off": min(0.25, float(self.settings["min_duration_off"])),
            })
        except Exception as exc:
            self._say(i18n.t("rec.split_failed", error=exc))
            return keys, names
        finally:
            ticking.set()
            path.unlink(missing_ok=True)

        keys, names = assign_room(session.lines, spans, self.settings.doc_lang)
        self._say(i18n.t("rec.voices_room", n=len(names)))
        return keys, names

    # --- кто именно говорил на той стороне --------------------------------

    def split_others(self, spk: np.ndarray,
                     session: Session | None = None) -> tuple[dict[int, str], dict[str, str]]:
        """Разбирает системную дорожку по голосам.

        Своя дорожка — это всегда я, а вот «собеседник» на созвоне почти
        никогда не один. Дорожки дают только «я / не я», поэтому по окончании
        звонка системный звук отдельно прогоняется через диаризацию: она
        работает по голосам и делит остальных на «Собеседник 1», «Собеседник 2»
        и так далее. Отдельно — потому что мой голос в этой дорожке отсутствует
        и не мешает кластеризации.

        Возвращает: номер реплики -> ключ спикера и ключи -> подписи.
        """
        session = session or self.session
        assert session is not None
        keys = {i: ("S1" if line.who == "me" else "S2")
                for i, line in enumerate(session.lines)}
        names = {"S1": i18n.d("me", self.settings.doc_lang),
                 "S2": i18n.d("them", self.settings.doc_lang)}

        theirs = [i for i, line in enumerate(session.lines) if line.who == "them"]
        if not self.settings.get("record_split_speakers", True) or len(theirs) < 2:
            return keys, names
        if spk.size < SAMPLE_RATE * 15:
            # Пятнадцати секунд чужой речи не хватит ни на какую кластеризацию.
            return keys, names

        path = session.directory / "sys-only.wav"
        ticking = threading.Event()
        started = time.time()

        def tick() -> None:
            while not ticking.wait(3):
                self._say(i18n.t("rec.splitting_call",
                                 seconds=int(time.time() - started)))

        try:
            _write_wav(path, spk)
            threading.Thread(target=tick, daemon=True).start()
            # Число говорящих здесь всегда определяем сами: настройка
            # «сколько спикеров» относится к разбору файлов целиком, а на
            # созвоне в неё не входит мой голос — она бы только сбивала.
            # Паузу между репликами берём короче обычной: на созвоне люди
            # перехватывают слово за доли секунды, и с порогом 0.5 с соседние
            # реплики разных людей склеиваются в одну.
            spans = diarize.diarize(path, {
                **self.settings,
                "num_speakers": 0,
                "min_duration_off": min(0.25, float(self.settings["min_duration_off"])),
            })
        except Exception as exc:
            self._say(i18n.t("rec.split_failed_call", error=exc))
            return keys, names
        finally:
            ticking.set()
            path.unlink(missing_ok=True)

        keys, names = assign_others(session.lines, spans, self.settings.doc_lang)
        if len(names) > 2:
            self._say(i18n.t("rec.voices_call", n=len(names) - 1))
        return keys, names

    def _retitle(self, session: Session, topic: str, out_dir: Path,
                 audio_path: Path) -> tuple[str, Path]:
        """Даёт записи осмысленное имя и переносит уже записанный звук."""
        title = f"{topic} {session.stamp}".strip() if session.stamp else topic
        stem = free_stem(out_dir, render.safe_stem(title))
        fresh = out_dir / f"{stem}.wav"
        if audio_path.exists() and fresh != audio_path:
            try:
                audio_path.replace(fresh)
            except OSError:
                return render.safe_stem(session.title), audio_path
        session.title = title
        session.renamed = True
        return stem, fresh

    # --- завершение ------------------------------------------------------

    def _finish(self, session: Session) -> None:
        session.state = "finishing"
        self._hold(session)
        self._say(i18n.t("rec.assembling"), session)

        mic = _to_float((session.directory / "mic.pcm").read_bytes()
                        if (session.directory / "mic.pcm").exists() else b"")
        spk = _to_float((session.directory / "sys.pcm").read_bytes()
                        if (session.directory / "sys.pcm").exists() else b"")
        size = max(mic.size, spk.size)
        if size == 0:
            raise RecordError(i18n.t("rec.empty"))
        mixed = np.zeros(size, dtype=np.float32)
        mixed[:mic.size] += mic
        mixed[:spk.size] += spk
        mixed = np.clip(mixed, -1.0, 1.0)

        out_dir = self.settings.output_path
        stem = render.safe_stem(session.title)
        audio_path = out_dir / f"{stem}.wav"
        _write_wav(audio_path, mixed)

        room = session.mode == "room"
        self._hold(session)
        keys, names = (self.split_room(mixed, session) if room
                       else self.split_others(spk, session))

        # Имена, расставленные по ходу, распространяем на всю запись по голосу.
        names = self.apply_names(mixed if room else spk, keys, names,
                                 only=None if room else "them", session=session)
        if not room:
            # Своя дорожка — это я по определению, голос сверять не нужно.
            mine = next((line.speaker for line in session.lines
                         if line.tagged and line.who == "me" and line.speaker), "")
            if mine:
                names["S1"] = mine

        for i, line in enumerate(session.lines):
            if line.tagged and line.speaker:
                # Человек уже сказал, кто это. Переносим реплику к тому голосу,
                # который получил это имя, а подпись не трогаем.
                match = next((k for k, v in names.items() if v == line.speaker), None)
                if match:
                    keys[i] = match
                continue
            line.speaker = names.get(keys.get(i, ""), "")

        transcript = asr.Transcript(
            segments=[asr.Segment(line.start, line.end, line.text, [],
                                  keys.get(i, "S1" if room else "S2"))
                      for i, line in enumerate(session.lines)],
            language=self.settings["language"], duration=size / SAMPLE_RATE,
            backend="live",
            model=i18n.t("rec.what_room" if room else "rec.what_call", "ru"),
        )
        turns = merge.build_turns(transcript)
        cleanup.clean_turns(turns, bool(self.settings.get("transcript_cleanup", True)))

        meta = {
            "title": session.title,
            "source": str(audio_path),
            "duration": size / SAMPLE_RATE,
            "language": self.settings["language"],
            "speakers": len(set(keys.values())) or 1,
            # Метка начала записи «2026-08-27 13-32» — из неё берётся дата,
            # к которой привязаны «завтра» и «до пятницы» в задачах.
            "recorded_at": session.stamp or render.now_stamp(),
            "processed_at": render.now_stamp(),
            "models": i18n.t("rec.what_room" if room else "rec.what_call", "ru"),
        }

        summary = None
        if self.settings["summary_enabled"] and session.lines:
            self._hold(session)
            # Молчащий спиннер выглядит как зависание, поэтому считаем секунды.
            ticking = threading.Event()
            started = time.time()

            def tick() -> None:
                while not ticking.wait(3):
                    self._say(i18n.t("rec.summarising",
                                     seconds=int(time.time() - started)), session)

            threading.Thread(target=tick, daemon=True).start()
            try:
                summary = summarize.summarize(
                    turns, self.settings, meta=meta, names=names,
                    preset=presets.resolve({**self.settings, "preset": session.preset}),
                )
                session.summary_md = summary.markdown
                session.summary_sections = summary.sections
                session.summary_tabs = [list(x) for x in summary.tabs]
                meta["models"] += f" + {summary.model}"
            except Exception as exc:
                session.message = i18n.t("warn.summary_failed", error=exc)
            finally:
                ticking.set()

        # «Созвон 2026-08-27 13-32» ничего не говорит через неделю. Спрашиваем
        # у модели тему разговора и ставим её перед датой — дата остаётся,
        # чтобы записи по-прежнему выстраивались по времени.
        if summary and not session.renamed:
            self._say(i18n.t("rec.naming"), session)
            topic = summarize.suggest_title(summary.markdown, self.settings)
            if topic:
                stem, audio_path = self._retitle(session, topic, out_dir, audio_path)
                meta["title"] = session.title
                meta["source"] = str(audio_path)

        session.files = render.write_all(out_dir, stem, transcript, turns, [],
                                         summary, meta, names,
                                         lang=self.settings.doc_lang)
        session.files["audio"] = str(audio_path)
        session.state = "done"
        session.message = i18n.t("state.done")
        self._emit(session=session)
        shutil.rmtree(session.directory, ignore_errors=True)


def free_stem(out_dir: Path, stem: str) -> str:
    """Не затираем чужую запись, если название совпало."""
    if not (out_dir / f"{stem}.result.json").exists() and not (out_dir / f"{stem}.wav").exists():
        return stem
    for n in range(2, 40):
        candidate = f"{stem} ({n})"
        if not (out_dir / f"{candidate}.result.json").exists():
            return candidate
    return stem


def _quiet_point(energy: np.ndarray, tail_seconds: float = 6.0) -> int:
    """Самое тихое место в конце куска — там и режем."""
    if energy.size < SAMPLE_RATE * 2:
        return energy.size
    window = SAMPLE_RATE // 10
    tail = int(tail_seconds * SAMPLE_RATE)
    start = max(window, energy.size - tail)
    usable = ((energy.size - start) // window) * window
    if usable < window * 2:
        return energy.size
    frames = energy[start:start + usable].reshape(-1, window).mean(axis=1)
    return start + int(np.argmin(frames)) * window
