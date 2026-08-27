"""Проверка пайплайна без тяжёлых моделей: подменяем Whisper и Ollama заглушками.

Запуск: python tools/selftest.py путь/к/файлу.mp4
Настоящая диаризация при этом работает по-честному.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import (  # noqa: E402
    asr,
    compute,
    diarize,
    edits,
    library,
    media,
    pipeline,
    presets,
    record,
    summarize,
)
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

        # Удаляем копию, а не саму запись: она ещё нужна следующим проверкам.
        stem = Path(entry["path"]).name[: -len(library.RESULT_SUFFIX)]
        for src_file in out_dir.glob(f"{stem}.*"):
            shutil.copy2(src_file, out_dir / src_file.name.replace(stem, f"{stem} копия"))
        copy = next((i for i in library.entries(out_dir) if "копия" in i["title"]
                     or "копия" in Path(i["path"]).name), None)
        failures += not check("копия записи появилась в архиве", copy is not None)
        if copy:
            gone = library.delete(out_dir, copy["id"])
            left = [i["id"] for i in library.entries(out_dir)]
            failures += not check("запись удаляется целиком",
                                  gone.get("ok") and copy["id"] not in left,
                                  f"файлов убрано: {gone.get('removed')}, "
                                  f"осталось записей: {len(left)}")

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
    room_lines = [record.Line(0, 4, "room", "раз"), record.Line(4, 9, "room", "два"),
                  record.Line(9, 14, "room", "три"), record.Line(14, 20, "room", "четыре")]
    room_spans = [diarize.SpeakerSpan(0, 4, 2), diarize.SpeakerSpan(4, 9, 0),
                  diarize.SpeakerSpan(9, 14, 2), diarize.SpeakerSpan(14, 20, 1)]
    room_keys, room_names = record.assign_room(room_lines, room_spans)
    failures += not check("встреча: голоса пронумерованы по первому появлению",
                          [room_keys[i] for i in range(4)] == ["S1", "S2", "S1", "S3"]
                          and room_names["S2"] == "Спикер 2",
                          " ".join(room_keys[i] for i in range(4)))
    empty_keys, empty_names = record.assign_room(room_lines, [])
    failures += not check("встреча без диаризации — один спикер",
                          set(empty_keys.values()) == {"S1"} and len(empty_names) == 1)
    failures += not check("подпись реплики на встрече пустая, пока не назвали",
                          record.Line(0, 1, "room", "x").label == ""
                          and record.Line(0, 1, "room", "x", speaker="Ирина").label == "Ирина")

    one, one_names = record.assign_others(lines, [diarize.SpeakerSpan(4, 31, 0)])
    failures += not check("один голос — остаётся просто «Собеседник»",
                          one_names == {"S1": "Я", "S2": "Собеседник"}
                          and one[5] == "S2")

    print("\n10. Имена участников по ходу записи")
    steno = record.Stenographer(settings)
    steno.session = record.Session(id="x", started_at=time.time(),
                                   directory=Path("/tmp/selftest-rec"), title="Проверка")
    steno.session.lines = [record.Line(0, 4, "them", "раз"),
                           record.Line(4, 9, "them", "два")]
    steno.set_people(["Ирина", " ирина ", "Дмитрий", ""])
    failures += not check("список участников без повторов и пустых",
                          steno.session.people == ["Ирина", "Дмитрий"],
                          ", ".join(steno.session.people))
    steno.tag(1, "Сергей")
    failures += not check("реплика отмечена именем",
                          steno.session.lines[1].speaker == "Сергей"
                          and steno.session.lines[1].tagged
                          and "Сергей" in steno.session.people)
    steno.tag(1, "")
    failures += not check("отметку можно снять",
                          not steno.session.lines[1].tagged
                          and steno.session.lines[1].label == "Собеседник")

    # Узнавание голоса на настоящем звуке: берём куски двух разных голосов,
    # один кусок каждого помечаем именем и смотрим, разойдутся ли имена верно.
    voiced = diarize.diarize(str(wav), settings)
    by_voice: dict[int, list] = {}
    for span in voiced:
        by_voice.setdefault(span.speaker, []).append(span)
    big = sorted(by_voice, key=lambda k: -sum(s.end - s.start for s in by_voice[k]))[:2]
    if len(big) < 2:
        failures += not check("в тестовой записи хватает голосов для проверки имён",
                              False, "нужны два разных голоса")
    else:
        lines, keys = [], {}
        for number, voice in enumerate(big):
            for span in [s for s in by_voice[voice] if s.end - s.start >= 1.2][:3]:
                keys[len(lines)] = f"S{number + 1}"
                lines.append(record.Line(span.start, span.end, "them", "речь"))
        steno.session.lines = lines
        names = {"S1": "Спикер 1", "S2": "Спикер 2"}
        # По одной отмеченной реплике на голос — как если бы человек кликнул
        # по ходу разговора.
        first = {key: i for i, key in sorted(keys.items(), reverse=True)}
        steno.tag(first["S1"], "Ирина")
        steno.tag(first["S2"], "Дмитрий")
        fresh = steno.apply_names(media.read_wav(wav), keys, names)
        failures += not check("имена разошлись по голосам верно",
                              fresh.get("S1") == "Ирина" and fresh.get("S2") == "Дмитрий",
                              f"S1={fresh.get('S1')}, S2={fresh.get('S2')}")
        # Отмечен только один человек — второму чужое имя доставаться не должно
        for line in lines:
            line.speaker, line.tagged = "", False
        steno.session.lines = lines
        steno.tag(first["S1"], "Ирина")
        alone = steno.apply_names(media.read_wav(wav), keys, dict(names))
        failures += not check("чужому голосу имя не достаётся",
                              alone.get("S1") == "Ирина" and alone.get("S2") == "Спикер 2",
                              f"S1={alone.get('S1')}, S2={alone.get('S2')}")

        for line in lines:
            line.speaker, line.tagged = "", False
        steno.session.lines = [record.Line(0, 4, "them", "раз")]
        untouched = steno.apply_names(media.read_wav(wav), {0: "S1"}, dict(names))
        failures += not check("без отметок имена не выдумываются",
                              untouched == names)

    print("\n11. Правка саммари вручную")
    out_dir = Path(settings["output_dir"])
    result_files = sorted(out_dir.glob("*.result.json"))
    if not result_files:
        failures += not check("есть что править", False)
    else:
        target = result_files[0]
        before = json.loads(target.read_text("utf-8"))
        sections = (before.get("summary") or {}).get("sections") or {}
        key = "tasks" if "tasks" in sections else next(iter(sections), "")
        transcript_file = target.with_name(target.name.replace(".result.json",
                                                               ".transcript.txt"))
        transcript_before = transcript_file.read_text("utf-8")

        kept = [line for line in sections.get(key, "").split("\n")
                if "Маркетинг" not in line]
        fresh = edits.apply(target, key, "\n".join(kept))
        failures += not check("мусорная строка убрана из раздела",
                              "Маркетинг" not in fresh["sections"][key])
        failures += not check("остальные разделы не тронуты",
                              set(fresh["sections"]) == set(sections))
        failures += not check("транскрипт остался как был",
                              transcript_file.read_text("utf-8") == transcript_before)
        summary_file = target.with_name(target.name.replace(".result.json", ".summary.md"))
        failures += not check("summary.md перезаписан",
                              "Маркетинг" not in summary_file.read_text("utf-8"))
        saved = json.loads(target.read_text("utf-8"))
        failures += not check("правка отмечена в json",
                              saved["summary"].get("edited") is True)

        # Смета: убрали строку — итог должен пересчитаться, а не остаться старым
        works = ("| Работа | Количество | Ставка | Стоимость |\n|---|---|---|---|\n"
                 "| Первая | 2 | 1000 | 2 000 |\n| Вторая | 3 | 1000 | 3 000 |")
        saved["summary"]["sections"] = {"works": works}
        saved["summary"]["tabs"] = [["works", "Работы"]]
        target.write_text(json.dumps(saved, ensure_ascii=False), "utf-8")
        edits.apply(target, "works", works)
        single = edits.apply(target, "works",
                             "\n".join(works.split("\n")[:3]))
        total_ok = "2" in single["sections"]["works"] and "5" not in \
            single["sections"]["works"].split("Итого")[-1].replace(" ", "")
        failures += not check("итог пересчитан после удаления строки", total_ok,
                              single["sections"]["works"].split("\n")[-1])

    print("\n12. Названия записей по теме разговора")
    for raw, want in [("Название: «Логика геймификации».", "Логика геймификации"),
                      ('"Ошибки 403 при выкатке".', "Ошибки 403 при выкатке"),
                      ("**Работа нейросетей**\nи ещё строка", "Работа нейросетей")]:
        failures += not check(f"чистка: {raw[:28]!r}",
                              summarize.clean_title(raw) == want,
                              summarize.clean_title(raw))
    failures += not check("слишком короткий ответ не берём",
                          summarize.suggest_title("ага", settings) == "")
    stemdir = Path("/tmp/selftest-stem")
    stemdir.mkdir(exist_ok=True)
    (stemdir / "Тема 2026.result.json").write_text("{}", "utf-8")
    failures += not check("имя занято — берём соседнее",
                          record.free_stem(stemdir, "Тема 2026") == "Тема 2026 (2)")

    print("\n13. Ошибки на плохом входе")
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
