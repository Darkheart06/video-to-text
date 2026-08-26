"""Проверка пайплайна без тяжёлых моделей: подменяем Whisper и Ollama заглушками.

Запуск: python tools/selftest.py путь/к/файлу.mp4
Настоящая диаризация при этом работает по-честному.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import asr, compute, diarize, library, media, pipeline, presets, record, summarize
from app.settings import Settings  # noqa: E402

WORDS = ["так", "давайте", "зафиксируем", "по", "срокам", "мы", "договорились", "что", "макет", "будет", "готов", "во", "вторник", "а", "тексты", "присылает", "отдел", "маркетинга", "до", "конца", "недели", "и", "тогда", "запускаем", "сборку", "версии", "для", "тестирования"]

FAKE_ANSWER = """## Краткое саммари
- Обсудили сроки запуска версии для тестирования.
- Договорились о готовности макета во вторник.

## Бриф
### Контекст
Рабочая встреча по подготовке релиза.
### Обсуждённые темы
- Сроки макета
- Тексты от маркетинга
### Ключевые тезисы
- Сборка стартует после получения текстов.

## Решения
- Макет сдаётся во вторник.

## Задачи
| Задача | Ответственный | Срок |
|---|---|---|
| Подготовить макет | Дизайн | вторник |
| Прислать тексты | Маркетинг | конец недели |

## Открытые вопросы и риски
- Не назначен ответственный за сборку.
"""

# Смета: модель заполняет только количество и ставку — стоимость считает код.
FAKE_ESTIMATE = """## Работы
| Работа | Количество | Единица | Ставка | Стоимость |
|---|---|---|---|---|
| Демонтаж перегородки | 12 | час | 3 000 | |
| Штробление стен | 8 | час | 2 500 | |
| Вывоз мусора | | рейс | 7 500 | |

## Условия
- Оплата по факту приёмки.

## Что уточнить
- Про двери цену не назвали.

## Кратко
- Смета на черновые работы.
"""


# --- заглушки ---------------------------------------------------------------

def fake_transcribe(wav_path, settings, progress=None):
    """Ровный поток слов по всей длине записи — чтобы проверить сведение."""
    audio = media.read_wav(wav_path)
    duration = len(audio) / media.SAMPLE_RATE
    step, t, i = 0.42, 0.3, 0
    segments, buf, seg_start = [], [], t
    while t < duration - step:
        word = WORDS[i % len(WORDS)]
        buf.append(asr.Word(t, t + step * 0.9, (" " if buf else "") + word))
        t += step
        i += 1
        if len(buf) >= 9:
            segments.append(asr.Segment(seg_start, buf[-1].end,
                                        "".join(w.text for w in buf).strip(), list(buf)))
            buf, seg_start = [], t
        if progress and i % 20 == 0:
            progress(min(0.99, t / duration), "Распознавание речи")
    if buf:
        segments.append(asr.Segment(seg_start, buf[-1].end,
                                    "".join(w.text for w in buf).strip(), list(buf)))
    if progress:
        progress(1.0, "Распознавание завершено")
    return asr.Transcript(segments, "ru", duration, "stub", "stub-model")


class FakeOllama(BaseHTTPRequestHandler):
    calls: list[str] = []

    def log_message(self, *_args):  # тишина в консоли
        pass

    def _send(self, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": "gemma4:12b-mlx"}, {"name": "qwen3:8b"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        prompt = payload["messages"][-1]["content"]
        FakeOllama.calls.append(prompt)
        answer = FAKE_ESTIMATE if "## Работы" in prompt else FAKE_ANSWER
        self._send({"message": {"role": "assistant", "content": answer}})


def check(name: str, condition: bool, detail: str = "") -> bool:
    mark = "\033[32m✓\033[0m" if condition else "\033[31m✗\033[0m"
    print(f"  {mark} {name}{(' — ' + detail) if detail else ''}")
    return condition


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src or not Path(src).exists():
        print("Укажите путь к тестовому медиафайлу")
        return 2

    failures = 0
    print("\n1. Медиа")
    info = media.probe(src)
    failures += not check("probe", info.duration > 1, f"{info.duration:.1f} с, видео={info.has_video}")
    wav = Path("/tmp/selftest.wav")
    media.extract_wav(src, wav)
    audio = media.read_wav(wav)
    failures += not check("извлечение звука", len(audio) > 16000,
                          f"{len(audio) / media.SAMPLE_RATE:.1f} с, 16 кГц моно")
    points = media.split_points(audio, target_seconds=20, search_seconds=5)
    failures += not check("нарезка на куски", points[0][0] == 0 and points[-1][1] == len(audio)
                          and all(a < b for a, b in points), f"{len(points)} шт.")

    print("\n2. Разбор markdown-разделов")
    sections = summarize.parse_sections(FAKE_ANSWER)
    failures += not check("все разделы найдены",
                          {"summary", "brief", "decisions", "tasks", "risks"} <= set(sections),
                          ", ".join(sections))
    failures += not check("таблица задач сохранилась", "Маркетинг" in sections.get("tasks", ""))

    print("\n3. Полный прогон (Whisper и Ollama — заглушки, спикеры — по-настоящему)")
    server = ThreadingHTTPServer(("127.0.0.1", 11888), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    settings = Settings.load()
    settings["llm_backend"] = "ollama"
    settings["ollama_url"] = "http://127.0.0.1:11888"
    settings["ollama_model"] = "auto"
    settings["output_dir"] = "/tmp/selftest-out"
    settings["summary_chunk_chars"] = 900     # заставляем сработать разбор по частям
    settings["cluster_threshold"] = 0.6

    asr.transcribe, real = fake_transcribe, asr.transcribe
    pipeline.asr.transcribe = fake_transcribe

    seen: list[tuple[str, float]] = []
    runner = pipeline.Runner(settings, listener=lambda j: seen.append((j.stage, j.progress)))
    job = runner.submit(src)
    while job.status in ("pending", "running"):
        time.sleep(0.2)

    failures += not check("задание завершилось", job.status == "done",
                          job.error or job.message)
    if job.status != "done":
        print(job.meta.get("traceback", ""))
        return 1

    failures += not check("прогресс монотонный",
                          all(b >= a for (_, a), (_, b) in zip(seen, seen[1:])),
                          f"{len(seen)} обновлений")
    failures += not check("этапы пройдены",
                          {"prepare", "asr", "diarize", "summary", "write"} <= {s for s, _ in seen},
                          " → ".join(dict.fromkeys(s for s, _ in seen)))
    failures += not check("спикеры найдены", len(job.speakers) >= 2,
                          ", ".join(f"{k}:{v['seconds']:.0f}с" for k, v in job.speakers.items()))
    failures += not check("реплики собраны", len(job.turns) >= 2, f"{len(job.turns)} реплик")
    failures += not check("саммари разобрано", bool(job.summary_sections.get("tasks")))
    failures += not check("разбор шёл по частям", len(FakeOllama.calls) >= 3,
                          f"{len(FakeOllama.calls)} запросов к модели")
    failures += not check("без предупреждений", not job.warnings, "; ".join(job.warnings))

    print("\n4. Выходные файлы")
    for path in job.files.values():
        p = Path(path)
        failures += not check(p.name, p.exists() and p.stat().st_size > 40,
                              f"{p.stat().st_size} байт" if p.exists() else "нет файла")
    srt_text = Path(job.files["subtitles"]).read_text("utf-8")
    failures += not check("srt: правильные таймкоды", "-->" in srt_text
                          and srt_text.split("\n")[1].count(":") == 4)
    data = json.loads(Path(job.files["result"]).read_text("utf-8"))
    failures += not check("json: разделы саммари", bool(data["summary"]["sections"]))
    failures += not check("json: реплики со спикерами",
                          all(t["speaker"] for t in data["turns"]))

    print("\n5. Переименование спикеров")
    first = next(iter(job.speakers))
    snap = runner.rename_speakers(job.id, {first: "Анна Петрова"})
    md_text = Path(job.files["transcript_md"]).read_text("utf-8")
    failures += not check("имя попало в транскрипт",
                          snap is not None and "Анна Петрова" in md_text)

    print("\n6. Отмена задания")
    job2 = runner.submit(src)
    time.sleep(0.4)
    runner.cancel(job2.id)
    for _ in range(60):
        if job2.status not in ("pending", "running"):
            break
        time.sleep(0.2)
    failures += not check("задание останавливается", job2.status == "cancelled",
                          f"{job2.status} {job2.stage} {job2.error}")
    if job2.status == "error":
        print(job2.meta.get("traceback", "")[-1200:])

    print("\n7. Профиль «Смета»: считает код, а не модель")
    est = presets.ESTIMATE
    result = compute.process(FAKE_ESTIMATE, "Смета")
    table = result.tables[0] if result.tables else None
    failures += not check("таблица найдена", table is not None)
    if table:
        expected = 12 * 3000 + 8 * 2500 + 1 * 7500
        failures += not check("итог посчитан", table.total == expected,
                              f"{table.total:.0f} = {expected}")
        failures += not check("строка без количества дополнена",
                              any(r[1] == "1" for r in table.rows))
        failures += not check("«Итого» дописано", "**Итого**" in result.markdown)
        csv_text = compute.to_csv(result.tables)
        failures += not check("csv без валюты и пробелов в числах",
                              ";3000;36000" in csv_text,
                              csv_text.splitlines()[2])
    est_sections = summarize.parse_sections(result.markdown, est.sections)
    failures += not check("разделы сметы разобраны",
                          {"works", "terms", "open", "summary"} <= set(est_sections),
                          ", ".join(est_sections))

    custom = presets.build_custom("свои правила", "")
    failures += not check("свой шаблон даёт вкладки", len(custom.sections) >= 2,
                          ", ".join(title for _, title in custom.sections))

    print("\n8. Архив записей")
    out_dir = Path(settings["output_dir"])
    items = library.entries(out_dir)
    failures += not check("запись попала в архив", len(items) >= 1,
                          ", ".join(i["title"] for i in items[:3]))
    if items:
        entry = items[0]
        failures += not check("в списке есть длительность и дата",
                              entry["duration"] > 1 and bool(entry["when"]),
                              f"{entry['when']} · {entry['duration']:.0f} с")
        failures += not check("в списке есть строка-подсказка", len(entry["preview"]) > 10,
                              entry["preview"][:60])
        word = "макет"
        failures += not check("поиск по тексту расшифровки",
                              any(i["id"] == entry["id"] for i in library.entries(out_dir, word)),
                              f"«{word}»")
        failures += not check("поиск не находит лишнего",
                              not library.entries(out_dir, "гидроэлектростанция"))
        snap = library.snapshot(out_dir, entry["id"])
        failures += not check("запись разворачивается обратно в карточку",
                              bool(snap) and snap["status"] == "done"
                              and len(snap["turns"]) > 3 and bool(snap["summary_sections"]))
        failures += not check("подписи вкладок сохранились",
                              bool(snap) and any(x[1] == "Задачи" for x in snap["summary_tabs"]),
                              ", ".join(x[1] for x in (snap or {}).get("summary_tabs", [])))
        failures += not check("файлы записи найдены рядом",
                              bool(snap) and {"summary", "transcript_md", "result"}
                              <= set(snap["files"]),
                              ", ".join(sorted((snap or {}).get("files", {}))))
        renamed = library.rename(out_dir, entry["id"], {"S1": "Ирина"})
        failures += not check(
            "имена спикеров меняются и в архиве",
            bool(renamed) and "Ирина" in json.dumps(renamed.get("speakers", {}),
                                                    ensure_ascii=False)
            and "Ирина" in (out_dir / (Path(entry["path"]).name[:-len(library.RESULT_SUFFIX)]
                                       + ".transcript.txt")).read_text("utf-8"))

        gone = library.delete(out_dir, entry["id"])
        left = [i["id"] for i in library.entries(out_dir)]
        failures += not check("запись удаляется целиком",
                              gone.get("ok") and entry["id"] not in left,
                              f"файлов убрано: {gone.get('removed')}, осталось записей: {len(left)}")

    print("\n9. Созвон: кто из собеседников говорил")
    lines = [
        record.Line(0, 4, "me", "Привет, все на месте?"),
        record.Line(4, 9, "them", "Да, привет"),
        record.Line(9, 14, "them", "И я тут"),
        record.Line(14, 18, "me", "Отлично, начнём"),
        record.Line(18, 25, "them", "У меня вопрос по срокам"),
        record.Line(25, 31, "them", "А я про бюджет"),
    ]
    spans = [diarize.SpeakerSpan(4, 9, 1), diarize.SpeakerSpan(9, 14, 0),
             diarize.SpeakerSpan(18, 25, 0), diarize.SpeakerSpan(25, 31, 1)]
    keys, names = record.assign_others(lines, spans)
    failures += not check("собеседники разделены",
                          names == {"S1": "Я", "S2": "Собеседник 1", "S3": "Собеседник 2"},
                          ", ".join(names.values()))
    failures += not check("своя дорожка осталась за мной",
                          all(keys[i] == "S1" for i, line in enumerate(lines)
                              if line.who == "me"))
    failures += not check("номера по первому появлению",
                          keys[1] == "S2" and keys[2] == "S3" and keys[5] == "S2",
                          " ".join(keys[i] for i in range(len(lines))))
    failures += not check("подписи попадают в реплики",
                          record.Line(0, 1, "them", "x", speaker="Собеседник 2").label
                          == "Собеседник 2")
    one, one_names = record.assign_others(lines, [diarize.SpeakerSpan(4, 31, 0)])
    failures += not check("один голос — остаётся просто «Собеседник»",
                          one_names == {"S1": "Я", "S2": "Собеседник"}
                          and one[5] == "S2")

    print("\n10. Ошибки на плохом входе")
    bad = runner.submit("/tmp/нет-такого-файла.mp4")
    for _ in range(40):
        if bad.status not in ("pending", "running"):
            break
        time.sleep(0.1)
    failures += not check("несуществующий файл", bad.status == "error", bad.error[:60])

    asr.transcribe = real
    server.shutdown()
    print(f"\n{'ВСЁ ХОРОШО' if not failures else f'ПРОБЛЕМ: {failures}'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
