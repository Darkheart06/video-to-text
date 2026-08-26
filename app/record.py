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

from . import asr, cleanup, diarize, llm, media, merge, presets, render, summarize
from .settings import WORK_DIR, Settings

SAMPLE_RATE = media.SAMPLE_RATE
BYTES_PER_SECOND = SAMPLE_RATE * 2

HELPER = media.BUNDLED_BIN / "v2t-capture"

Listener = Callable[[str, dict], None]


class RecordError(RuntimeError):
    pass


# macOS сообщает об отказе в записи экрана кодом -3801 и формулировкой,
# по которой невозможно догадаться, что делать. Переводим на человеческий.
DENIED = ("-3801", "SCStreamErrorDomain Code=-3801", "declined")


def friendly_error(text: str) -> str:
    if any(mark in text for mark in DENIED):
        return (
            "macOS не разрешила записывать экран, а без этого не слышно "
            "собеседников. Откройте «Конфиденциальность и безопасность» → "
            "«Запись экрана», включите «Расшифровка» и запустите приложение заново."
        )
    return text.strip()[-300:] or "Захват звука не запустился"


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

    @property
    def label(self) -> str:
        return self.speaker or ("Я" if self.who == "me" else "Собеседник")


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
            "lines": [
                {"start": round(line.start, 1), "who": line.who,
                 "label": line.label, "text": line.text}
                for line in self.lines[-tail:]
            ],
            "line_count": len(self.lines),
        }


# --- разрешения и наличие помощника ------------------------------------------

def helper_ready() -> bool:
    return HELPER.exists()


def permissions() -> dict:
    """Что macOS уже разрешила: запись экрана (звук системы) и микрофон."""
    if not helper_ready():
        return {"screen": False, "microphone": False,
                "error": "Помощник захвата не установлен"}
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
                "error": "Помощник захвата не установлен"}
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


def assign_others(lines: list[Line],
                  spans: list) -> tuple[dict[int, str], dict[str, str]]:
    """Раскладывает реплики собеседников по найденным голосам.

    Своя дорожка не участвует: «Я» и так известен. Номера даём по первому
    появлению в разговоре, чтобы «Собеседник 1» был тем, кто заговорил раньше.
    """
    keys = {i: ("S1" if line.who == "me" else "S2") for i, line in enumerate(lines)}
    names = {"S1": "Я", "S2": "Собеседник"}
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
    names = {"S1": "Я"}
    for number in range(len(order)):
        names[f"S{number + 2}"] = f"Собеседник {number + 1}"
    return keys, names


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
           "так", "там", "уже", "щас", "сейчас", "получается", "короче"}


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

    # --- события ---------------------------------------------------------

    def _emit(self, event: str = "record") -> None:
        if self.listener and self.session:
            try:
                self.listener(event, self.session.snapshot())
            except Exception:
                pass

    def _say(self, message: str) -> None:
        if self.session:
            self.session.message = message
            self._emit()

    # --- управление ------------------------------------------------------

    def is_active(self) -> bool:
        return self.session is not None and self.session.state in ("recording", "finishing")

    def start(self, title: str = "", preset: str = "") -> dict:
        with self._lock:
            if self.is_active():
                raise RecordError("Запись уже идёт")
            if not helper_ready():
                raise RecordError(
                    "Помощник захвата не установлен — переустановите приложение."
                )
            # Предварительную проверку разрешения не делаем: macOS отвечает на
            # неё неточно. Пробуем начать по-настоящему — отказ придёт от самой
            # системы, и уже понятной формулировкой.

            session_id = uuid.uuid4().hex[:10]
            directory = WORK_DIR / f"rec-{session_id}"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H-%M")
            self.session = Session(
                id=session_id, started_at=time.time(), directory=directory,
                title=title.strip() or f"Созвон {stamp}",
                preset=preset or self.settings.get("preset", presets.DEFAULT),
            )
            self._stop.clear()
            self._process = subprocess.Popen(
                [str(HELPER), "record", str(directory)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()
            self._say("Идёт запись")
            return self.session.snapshot()

    def stop(self) -> dict | None:
        with self._lock:
            if not self.session or self.session.state != "recording":
                return self.session.snapshot() if self.session else None
            self.session.state = "finishing"
            self._say("Завершаю запись")
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
            self.session.message = "Запись отменена"
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
                ready = min(mic_path.stat().st_size if mic_path.exists() else 0,
                            sys_path.stat().st_size if sys_path.exists() else 0)
                if ready - offset >= chunk_bytes:
                    offset = self._consume(mic_path, sys_path, offset, ready)
                    if session.duration >= next_note:
                        self._make_note()
                        next_note += notes_every
                time.sleep(1.0)

            # Остановка: забираем хвост и собираем результат
            self._terminate_helper()
            time.sleep(0.6)
            ready = min(mic_path.stat().st_size if mic_path.exists() else 0,
                        sys_path.stat().st_size if sys_path.exists() else 0)
            if ready > offset:
                self._consume(mic_path, sys_path, offset, ready, final=True)
            self._finish()

        except Exception as exc:
            self._terminate_helper()
            session.state = "error"
            session.error = str(exc)
            session.message = "Ошибка"
            self._emit()

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

        mic = _to_float(_read_tail(mic_path, offset)[:length])
        spk = _to_float(_read_tail(sys_path, offset)[:length])
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
        for track, who in ((mic, "me"), (spk, "them")):
            if not _has_speech(track):
                continue
            fresh.extend(self._transcribe(track, base, who))

        if self.settings.get("record_dedupe", True):
            fresh = drop_echo(fresh, mic, spk, base)
        cleanup.clean_turns(fresh, bool(self.settings.get("transcript_cleanup", True)))

        session.lines.extend(fresh)
        session.lines.sort(key=lambda item: item.start)
        self._say(f"Записано {int(session.duration // 60)} мин, "
                  f"реплик {len(session.lines)}")
        return offset + length

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
            answer = backend.chat(
                summarize.SYSTEM,
                "Идёт созвон. Ниже — то, что прозвучало за последние минуты.\n"
                "Выпиши коротко: 2–4 пункта о чём говорили и отдельно новые "
                "задачи и договорённости, если они были. Без вступлений.\n\n"
                + text,
            )
        except Exception:
            return
        session.notes.append({
            "at": round(session.duration),
            "text": answer.strip(),
            "covered": list(range(seen, len(session.lines))),
        })
        self._emit()

    # --- кто именно говорил на той стороне --------------------------------

    def split_others(self, spk: np.ndarray) -> tuple[dict[int, str], dict[str, str]]:
        """Разбирает системную дорожку по голосам.

        Своя дорожка — это всегда я, а вот «собеседник» на созвоне почти
        никогда не один. Дорожки дают только «я / не я», поэтому по окончании
        звонка системный звук отдельно прогоняется через диаризацию: она
        работает по голосам и делит остальных на «Собеседник 1», «Собеседник 2»
        и так далее. Отдельно — потому что мой голос в этой дорожке отсутствует
        и не мешает кластеризации.

        Возвращает: номер реплики -> ключ спикера и ключи -> подписи.
        """
        session = self.session
        assert session is not None
        keys = {i: ("S1" if line.who == "me" else "S2")
                for i, line in enumerate(session.lines)}
        names = {"S1": "Я", "S2": "Собеседник"}

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
                self._say("Разбираю, кто из собеседников говорил — "
                          f"{int(time.time() - started)} с")

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
            self._say(f"Голоса собеседников не разобраны: {exc}")
            return keys, names
        finally:
            ticking.set()
            path.unlink(missing_ok=True)

        keys, names = assign_others(session.lines, spans)
        if len(names) > 2:
            self._say(f"Собеседников на звонке: {len(names) - 1}")
        return keys, names

    # --- завершение ------------------------------------------------------

    def _finish(self) -> None:
        session = self.session
        assert session is not None
        session.state = "finishing"
        self._say("Собираю запись")

        mic = _to_float((session.directory / "mic.pcm").read_bytes()
                        if (session.directory / "mic.pcm").exists() else b"")
        spk = _to_float((session.directory / "sys.pcm").read_bytes()
                        if (session.directory / "sys.pcm").exists() else b"")
        size = max(mic.size, spk.size)
        if size == 0:
            raise RecordError("Записать звук не удалось — дорожки пустые")
        mixed = np.zeros(size, dtype=np.float32)
        mixed[:mic.size] += mic
        mixed[:spk.size] += spk
        mixed = np.clip(mixed, -1.0, 1.0)

        out_dir = self.settings.output_path
        stem = render.safe_stem(session.title)
        audio_path = out_dir / f"{stem}.wav"
        _write_wav(audio_path, mixed)

        keys, names = self.split_others(spk)
        for i, line in enumerate(session.lines):
            line.speaker = names.get(keys.get(i, ""), "")

        transcript = asr.Transcript(
            segments=[asr.Segment(line.start, line.end, line.text, [],
                                  keys.get(i, "S2"))
                      for i, line in enumerate(session.lines)],
            language=self.settings["language"], duration=size / SAMPLE_RATE,
            backend="live", model="запись созвона",
        )
        turns = merge.build_turns(transcript)
        cleanup.clean_turns(turns, bool(self.settings.get("transcript_cleanup", True)))

        meta = {
            "title": session.title,
            "source": str(audio_path),
            "duration": size / SAMPLE_RATE,
            "language": self.settings["language"],
            "speakers": len({keys.get(i, "S2") for i in range(len(session.lines))}) or 2,
            "processed_at": render.now_stamp(),
            "models": "запись созвона",
        }

        summary = None
        if self.settings["summary_enabled"] and session.lines:
            # Молчащий спиннер выглядит как зависание, поэтому считаем секунды.
            ticking = threading.Event()
            started = time.time()

            def tick() -> None:
                while not ticking.wait(3):
                    self._say("Готовлю саммари и бриф — "
                              f"{int(time.time() - started)} с")

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
                session.message = f"Саммари не составлено: {exc}"
            finally:
                ticking.set()

        session.files = render.write_all(out_dir, stem, transcript, turns, [],
                                         summary, meta, names)
        session.files["audio"] = str(audio_path)
        session.state = "done"
        session.message = "Готово"
        self._emit()
        shutil.rmtree(session.directory, ignore_errors=True)


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
