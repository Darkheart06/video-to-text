"""Оркестрация: файл → аудио → распознавание → спикеры → саммари → файлы."""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import agenda as agenda_module
from . import (
    asr,
    attach,
    cleanup,
    diarize,
    i18n,
    media,
    merge,
    presets,
    render,
    summarize,
    voices,
)
from . import marks as marks_module
from .settings import WORK_DIR, Settings

Listener = Callable[["Job"], None]


class Cancelled(RuntimeError):
    pass


# Доли шкалы прогресса по этапам — чтобы полоска шла ровно, а не рывками.
STAGES_FULL = [("prepare", 0.03), ("asr", 0.55), ("diarize", 0.17),
               ("summary", 0.22), ("write", 0.03)]


@dataclass
class Job:
    id: str
    source: str
    title: str
    status: str = "pending"       # pending | running | done | error | cancelled
    stage: str = ""
    message: str = ""
    progress: float = 0.0
    error: str = ""
    files: dict[str, str] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    summary_md: str = ""
    summary_sections: dict = field(default_factory=dict)
    summary_tabs: list = field(default_factory=list)
    preset: str = ""
    transcript_md: str = ""
    turns: list = field(default_factory=list)
    speakers: dict = field(default_factory=dict)
    marks: list = field(default_factory=list)
    next_call: dict | None = None   # договорённость о следующем созвоне
    warnings: list[str] = field(default_factory=list)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict:
        return {
            "id": self.id, "source": self.source, "title": self.title,
            "status": self.status, "stage": self.stage, "message": self.message,
            "progress": round(self.progress, 4), "error": self.error,
            "files": self.files, "meta": self.meta,
            "summary_md": self.summary_md, "summary_sections": self.summary_sections,
            "summary_tabs": self.summary_tabs, "preset": self.preset,
            "transcript_md": self.transcript_md,
            "speakers": self.speakers, "marks": self.marks,
            "next_call": self.next_call,
            "warnings": self.warnings,
            "turns": [
                {"start": t.start, "end": t.end, "speaker": t.speaker_key, "text": t.text}
                for t in self.turns
            ],
        }


def _recorded_at(source: str) -> str:
    """Когда запись сделана — по времени файла, если оно вообще есть."""
    try:
        stamp = Path(source).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")


class Runner:
    """Хранит задания и запускает обработку в фоновом потоке."""

    def __init__(self, settings: Settings, listener: Listener | None = None) -> None:
        self.settings = settings
        self.listener = listener
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # --- управление -------------------------------------------------------

    def submit(self, source: str, preset: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], source=str(source),
                  title=Path(source).name,
                  preset=preset or self.settings.get("preset", presets.DEFAULT))
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job and job.status in ("pending", "running"):
            job._cancel.set()
            return True
        return False

    def _emit(self, job: Job) -> None:
        if self.listener:
            try:
                self.listener(job)
            except Exception:
                pass

    def _update(self, job: Job, *, stage: str | None = None, message: str | None = None,
                progress: float | None = None, status: str | None = None) -> None:
        if job._cancel.is_set():
            raise Cancelled()
        if stage is not None:
            job.stage = stage
        if message is not None:
            job.message = message
        if progress is not None:
            job.progress = max(job.progress, min(1.0, progress))
        if status is not None:
            job.status = status
        self._emit(job)

    def _stage_progress(self, job: Job, stage: str, base: float, span: float):
        def cb(frac: float, msg: str) -> None:
            self._update(job, stage=stage, message=msg, progress=base + span * frac)
        return cb

    # --- основной сценарий ------------------------------------------------

    def _run(self, job: Job) -> None:
        wav: Path | None = None
        try:
            s = self.settings
            stages = dict(STAGES_FULL)
            if not s["diarization_enabled"]:
                stages["asr"] += stages.pop("diarize")
                stages["diarize"] = 0.0
            if not s["summary_enabled"]:
                stages["asr"] += stages.pop("summary")
                stages["summary"] = 0.0

            bounds, acc = {}, 0.0
            for name, weight in [(n, stages.get(n, 0.0)) for n, _ in STAGES_FULL]:
                bounds[name] = (acc, weight)
                acc += weight

            self._update(job, status="running", stage="prepare",
                         message=i18n.t("stage.check"), progress=0.005)
            info = media.probe(job.source)

            WORK_DIR.mkdir(parents=True, exist_ok=True)
            wav = WORK_DIR / f"{job.id}.wav"
            self._update(job, message=i18n.t("stage.audio"),
                         progress=bounds["prepare"][0] + bounds["prepare"][1] * 0.3)
            media.extract_wav(job.source, wav)

            # 1. Распознавание
            base, span = bounds["asr"]
            transcript = asr.transcribe(
                wav, s, progress=self._stage_progress(job, "asr", base, span)
            )
            if not transcript.segments:
                raise RuntimeError(i18n.t("warn.no_speech"))

            # 2. Спикеры
            spans: list[diarize.SpeakerSpan] = []
            if s["diarization_enabled"]:
                base, span = bounds["diarize"]
                try:
                    spans = diarize.diarize(
                        wav, s, progress=self._stage_progress(job, "diarize", base, span)
                    )
                    merge.assign_speakers(transcript, spans)
                except Cancelled:
                    raise
                except Exception as exc:
                    job.warnings.append(i18n.t("warn.diar_failed", error=exc))
                    self._update(job, message=i18n.t("warn.diar_skip"))

            turns = merge.build_turns(transcript)
            cleanup.clean_turns(turns, bool(s["transcript_cleanup"]))
            job.turns = turns
            speaking = diarize.speaking_time(spans)
            job.speakers = {
                f"S{spk + 1}": {"label": i18n.d("speaker", s.doc_lang, n=spk + 1),
                                "seconds": round(sec, 1)}
                for spk, sec in speaking.items()
            }

            # Знакомые голоса: кого запомнили командой «Запомнить голоса»,
            # того подписываем именем сразу — и в транскрипте, и в саммари.
            names: dict[str, str] = {}
            voice_model = diarize.emb_choice(s)
            known = (voices.load(voice_model)
                     if spans and s.get("known_voices", True) else {})
            if known:
                # Звук и модель голосов поднимаем только когда есть с кем
                # сравнивать: пустая память не должна стоить ни секунды.
                try:
                    for spk, name in voices.identify(
                            media.read_wav(wav), spans, int(s["num_threads"]),
                            people=known, model=voice_model).items():
                        key = f"S{spk + 1}"
                        if key in job.speakers:
                            job.speakers[key]["label"] = name
                            names[key] = name
                except Exception as exc:      # память — приятная мелочь, не повод падать
                    job.warnings.append(i18n.t("warn.voices_failed", error=exc))

            meta = {
                "title": Path(job.source).stem,
                "source": job.source,
                "duration": info.duration or transcript.duration,
                "language": transcript.language,
                "speakers": len(job.speakers) or "—",
                # Дата самой записи, а не разбора: файл могли принести через
                # неделю, а «завтра» в разговоре означало завтра после встречи.
                "recorded_at": _recorded_at(job.source),
                "processed_at": render.now_stamp(),
                "models": f"{transcript.model} ({transcript.backend})",
            }
            job.meta = meta
            job.transcript_md = render.transcript_markdown(turns, meta, names)
            self._emit(job)

            # 3. Саммари
            summary = None
            if s["summary_enabled"]:
                base, span = bounds["summary"]
                try:
                    summary = summarize.summarize(
                        turns, s, meta=meta, names=names,
                        progress=self._stage_progress(job, "summary", base, span),
                        preset=presets.resolve({**s, "preset": job.preset}),
                    )
                    job.summary_md = summary.markdown
                    job.summary_sections = summary.sections
                    job.summary_tabs = [list(x) for x in summary.tabs]
                    meta["models"] += f" + {summary.model}"
                except Cancelled:
                    raise
                except Exception as exc:
                    job.warnings.append(i18n.t("warn.summary_failed", error=exc))
                    self._update(job, message=i18n.t("warn.summary_skip"))

            # 4. Файлы
            base, span = bounds["write"]
            self._update(job, stage="write", message=i18n.t("stage.save"),
                         progress=base + span * 0.2)
            stem = render.safe_stem(Path(job.source).stem)
            job.marks = marks_module.build(turns, job.summary_sections, [], s.doc_lang)
            try:
                hint = agenda_module.suggest(
                    {"sections": job.summary_sections},
                    str(job.meta.get("recorded_at") or ""), s.doc_lang)
                if hint:
                    job.next_call = hint
                    job.meta["next_call"] = hint
            except Exception:
                pass
            job.files = render.write_all(
                s.output_path, stem, transcript, turns, spans, summary, meta,
                names=names, lang=s.doc_lang, marks=job.marks
            )

            self._update(job, stage="done", message=i18n.t("state.done"),
                         progress=1.0, status="done")

        except Cancelled:
            job.status = "cancelled"
            job.message = i18n.t("state.cancelled")
            self._emit(job)
        except Exception as exc:
            if job._cancel.is_set():
                # Отмена могла прилететь изнутри чужой библиотеки и превратиться
                # там в свою ошибку — для пользователя это всё равно отмена.
                job.status = "cancelled"
                job.message = i18n.t("state.cancelled")
                self._emit(job)
                return
            job.status = "error"
            job.error = str(exc) or exc.__class__.__name__
            job.message = i18n.t("state.error")
            job.meta.setdefault("traceback", traceback.format_exc()[-2000:])
            self._emit(job)
        finally:
            if wav and wav.exists() and not self.settings["keep_wav"]:
                try:
                    wav.unlink()
                except OSError:
                    pass

    # --- пересборка саммари с приложенными документами ---------------------

    def resummarize(self, result_path: str, preset: str = "") -> Job:
        """Собирает саммари заново — теперь уже с документами, которые к записи
        приложили после разбора.

        Расшифровку не трогаем: она не изменилась. Меняется только документ,
        который из неё делают, и это ровно то, чего человек ждёт, приложив к
        созвону смету или техзадание.
        """
        path = Path(result_path)
        data = json.loads(path.read_text("utf-8"))
        meta = dict(data.get("meta") or {})
        job = Job(id=uuid.uuid4().hex[:12], source=str(path),
                  title=str(meta.get("title") or path.stem),
                  preset=preset or (data.get("summary") or {}).get("preset")
                  or self.settings.get("preset", presets.DEFAULT))
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._resummary, args=(job, path, data),
                         daemon=True).start()
        return job

    def _resummary(self, job: Job, path: Path, data: dict) -> None:
        from . import library

        try:
            s = self.settings
            self._update(job, status="running", stage="summary",
                         message=i18n.t("stage.check"), progress=0.05)
            turns = library._turns_of(data)
            job.turns = turns
            speakers = data.get("speakers") or {}
            names = {key: value.get("label", key) for key, value in speakers.items()}
            job.speakers = {key: {"label": value.get("label", key),
                                  "seconds": float(value.get("speaking_seconds") or 0)}
                            for key, value in speakers.items()}
            meta = dict(data.get("meta") or {})
            context = attach.context(path)
            meta["files"] = attach.names(path)
            job.meta = meta

            summary = summarize.summarize(
                turns, s, meta=meta, names=names, context=context,
                progress=self._stage_progress(job, "summary", 0.1, 0.8),
                preset=presets.resolve({**s, "preset": job.preset}),
            )
            job.summary_md = summary.markdown
            job.summary_sections = summary.sections
            job.summary_tabs = [list(x) for x in summary.tabs]

            self._update(job, stage="write", message=i18n.t("stage.save"), progress=0.92)
            stem = path.name[: -len(library.RESULT_SUFFIX)]
            transcript = asr.Transcript(segments=[], language=str(meta.get("language") or ""),
                                        duration=float(meta.get("duration") or 0),
                                        backend="", model=str(meta.get("models") or ""))
            job.marks = marks_module.build(turns, job.summary_sections, [], s.doc_lang)
            try:
                hint = agenda_module.suggest(
                    {"sections": job.summary_sections},
                    str(job.meta.get("recorded_at") or ""), s.doc_lang)
                if hint:
                    job.next_call = hint
                    job.meta["next_call"] = hint
            except Exception:
                pass
            job.files = render.write_all(path.parent, stem, transcript, turns, [],
                                         summary, meta, names=names, lang=s.doc_lang,
                                         marks=job.marks)
            library.forget_cache(path)
            self._update(job, stage="done", message=i18n.t("state.done"),
                         progress=1.0, status="done")
        except Exception as exc:
            job.status = "error"
            job.error = str(exc) or exc.__class__.__name__
            job.message = i18n.t("state.error")
            job.meta.setdefault("traceback", traceback.format_exc()[-2000:])
            self._emit(job)

    # --- переименование спикеров -----------------------------------------

    def rename_speakers(self, job_id: str, names: dict[str, str]) -> dict | None:
        """Меняет подписи спикеров и перезаписывает текстовые файлы."""
        job = self.jobs.get(job_id)
        if not job or job.status != "done" or not job.turns:
            return None
        names = {k: v.strip() for k, v in names.items() if v and v.strip()}
        for key, info in job.speakers.items():
            info["label"] = names.get(key, info["label"])

        lang = self.settings.doc_lang
        job.transcript_md = render.transcript_markdown(job.turns, job.meta, names, lang)
        out_dir = Path(next(iter(job.files.values()))).parent if job.files \
            else self.settings.output_path
        stem = render.safe_stem(Path(job.source).stem)
        (out_dir / f"{stem}.transcript.md").write_text(job.transcript_md, "utf-8")
        (out_dir / f"{stem}.transcript.txt").write_text(
            render.plain_transcript(job.turns, names, lang), "utf-8")
        (out_dir / f"{stem}.subtitles.srt").write_text(
            render.srt(job.turns, names, lang=lang), "utf-8")
        # В архиве подписи берутся из result.json — обновляем и его, иначе
        # запись, открытая позже, снова окажется со «Спикером 1».
        result = out_dir / f"{stem}.result.json"
        if result.exists():
            try:
                data = json.loads(result.read_text("utf-8"))
                for key, value in (data.get("speakers") or {}).items():
                    value["label"] = job.speakers.get(key, {}).get("label", value.get("label"))
                result.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            except Exception:
                pass
        self._emit(job)
        return job.snapshot()
