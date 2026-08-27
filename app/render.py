"""Формирование выходных файлов: транскрипт, саммари, субтитры, JSON."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import i18n
from .asr import Transcript
from .diarize import SpeakerSpan, speaking_time
from .merge import Turn
from .summarize import Summary


def hhmmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def speaker_label(turn: Turn, names: dict[str, str] | None = None, lang: str = "") -> str:
    names = names or {}
    lang = i18n.pick(lang, i18n.current())
    if turn.speaker is None:
        return names.get("unknown", i18n.d("unknown", lang))
    return names.get(turn.speaker_key, i18n.d("speaker", lang, n=turn.speaker + 1))


def transcript_markdown(turns: list[Turn], meta: dict,
                        names: dict[str, str] | None = None, lang: str = "") -> str:
    lang = i18n.pick(lang, i18n.current())
    title = meta.get("title") or i18n.d("recording", lang)
    lines = [i18n.d("transcript_title", lang, title=title), ""]
    lines += _meta_block(meta, lang)
    lines.append("")
    for t in turns:
        lines.append(f"**[{hhmmss(t.start)}] {speaker_label(t, names, lang)}:** {t.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def plain_transcript(turns: list[Turn], names: dict[str, str] | None = None,
                     lang: str = "") -> str:
    return "\n".join(
        f"[{hhmmss(t.start)}] {speaker_label(t, names, lang)}: {t.text}" for t in turns
    ) + "\n"


def summary_markdown(summary: Summary, meta: dict, lang: str = "") -> str:
    lang = i18n.pick(lang, i18n.current())
    title = meta.get("title") or i18n.d("recording", lang)
    lines = [i18n.d("summary_title", lang, title=title), ""]
    lines += _meta_block(meta, lang)
    lines.append("")
    lines.append(summary.markdown.strip())
    return "\n".join(lines).rstrip() + "\n"


def _meta_block(meta: dict, lang: str = "") -> list[str]:
    rows = [
        (i18n.d("meta.source", lang), meta.get("source", "—")),
        (i18n.d("meta.duration", lang), hhmmss(meta.get("duration", 0))),
        (i18n.d("meta.language", lang), meta.get("language", "—")),
        (i18n.d("meta.speakers", lang), meta.get("speakers", "—")),
        (i18n.d("meta.processed", lang), meta.get("processed_at", "—")),
        (i18n.d("meta.models", lang), meta.get("models", "—")),
    ]
    out = ["| | |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in rows]
    return out


def srt(turns: list[Turn], names: dict[str, str] | None = None,
        max_chars: int = 90, lang: str = "") -> str:
    blocks, idx = [], 1
    for t in turns:
        label = speaker_label(t, names, lang)
        for start, end, text in _split_turn(t, max_chars):
            blocks.append(
                f"{idx}\n{srt_time(start)} --> {srt_time(end)}\n{label}: {text}\n"
            )
            idx += 1
    return "\n".join(blocks)


def _split_turn(turn: Turn, max_chars: int):
    """Режет длинную реплику на строки субтитров по границам сегментов."""
    if len(turn.text) <= max_chars or not turn.segments:
        yield turn.start, turn.end, turn.text
        return
    buf, start, end = "", None, None
    for seg in turn.segments:
        if start is None:
            start = seg.start
        candidate = f"{buf} {seg.text}".strip()
        if buf and len(candidate) > max_chars:
            yield start, end or seg.start, buf
            buf, start, end = seg.text, seg.start, seg.end
        else:
            buf, end = candidate, seg.end
    if buf:
        yield start or turn.start, end or turn.end, buf


def result_json(transcript: Transcript, turns: list[Turn], spans: list[SpeakerSpan],
                summary: Summary | None, meta: dict,
                names: dict[str, str] | None = None, lang: str = "") -> str:
    payload = {
        "meta": meta,
        # У записанного созвона диаризации нет — говорящие известны по дорожкам.
        # Считаем их по репликам, иначе запись в архиве осталась бы без имён.
        "speakers": _speakers_block(turns, spans, names, lang),
        # Профиль и подписи вкладок нужны архиву: по одним ключам разделов
        # не всегда понятно, как их назвать в окне.
        "summary": ({"model": summary.model, "markdown": summary.markdown,
                     "sections": summary.sections,
                     "preset": getattr(summary, "preset", ""),
                     "tabs": [list(x) for x in getattr(summary, "tabs", [])]}
                    if summary else None),
        "turns": [
            {
                "start": round(t.start, 2), "end": round(t.end, 2),
                "speaker": t.speaker_key, "text": t.text,
                # исходная реплика со всеми «ну» и «вот» — на случай, если
                # понадобится дословно
                **({"raw": t.raw} if getattr(t, "raw", "") and t.raw != t.text else {}),
            }
            for t in turns
        ],
        "segments": [
            {
                "start": round(s.start, 2), "end": round(s.end, 2),
                "speaker": s.speaker, "text": s.text,
            }
            for s in transcript.segments
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _speakers_block(turns: list[Turn], spans: list[SpeakerSpan],
                    names: dict[str, str] | None, lang: str = "") -> dict:
    names = names or {}
    seconds: dict[str, float] = {}
    if spans:
        seconds = {f"S{spk + 1}": sec for spk, sec in speaking_time(spans).items()}
    else:
        for turn in turns:
            if turn.speaker is None:
                continue
            key = turn.speaker_key
            seconds[key] = seconds.get(key, 0.0) + max(0.0, turn.end - turn.start)
    return {
        key: {"label": names.get(key, i18n.d("speaker", lang, n=key[1:])),
              "speaking_seconds": round(sec, 1)}
        for key, sec in sorted(seconds.items())
    }


def write_all(out_dir: Path, stem: str, transcript: Transcript, turns: list[Turn],
              spans: list[SpeakerSpan], summary: Summary | None, meta: dict,
              names: dict[str, str] | None = None, lang: str = "") -> dict[str, str]:
    lang = i18n.pick(lang, i18n.current())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    def write(key: str, suffix: str, content: str) -> None:
        path = out_dir / f"{stem}{suffix}"
        path.write_text(content, "utf-8")
        files[key] = str(path)

    if summary:
        write("summary", ".summary.md", summary_markdown(summary, meta, lang))
        # Таблицу с ценами удобнее открыть в Numbers или Excel, чем читать в md
        if getattr(summary, "csv", ""):
            write("tables", i18n.d("out.tables", lang), summary.csv)
    write("transcript_md", ".transcript.md",
          transcript_markdown(turns, meta, names, lang))
    write("transcript_txt", ".transcript.txt", plain_transcript(turns, names, lang))
    write("subtitles", ".subtitles.srt", srt(turns, names, lang=lang))
    write("result", ".result.json",
          result_json(transcript, turns, spans, summary, meta, names, lang))
    return files


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def safe_stem(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_.()[]" else "_" for c in name)
    return keep.strip().rstrip(".")[:120] or "record"
