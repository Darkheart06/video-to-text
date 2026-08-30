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
    dates,
    diarize,
    edits,
    i18n,
    library,
    media,
    merge,
    pipeline,
    presets,
    record,
    render,
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



def settings_output_name(settings) -> str:
    """Имя папки с результатами при пустом output_dir — без создания папки."""
    from app.settings import output_dir_for

    return output_dir_for(settings.doc_lang).name


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

    # Проверки ниже написаны по-русски и сверяют русские подписи, поэтому язык
    # задаём явно: иначе на английской системе тест «упадёт» из-за перевода,
    # а не из-за поломки. Английскую сторону проверяет отдельный раздел 16.
    i18n.use("ru")
    settings = Settings.load()
    settings["ui_language"] = "ru"
    settings["doc_language"] = "ru"
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

    # Порог сведения голосов считается по самой записи — проверяем правило,
    # а не сам звук: ниже настроенного числа порог опускаться не должен.
    failures += not check("порог по записи: устойчивые голоса — строже",
                          diarize.auto_limit([0.97, 0.96, 0.95], {}) > 0.78,
                          f"{diarize.auto_limit([0.97, 0.96, 0.95], {})}")
    failures += not check("порог по записи: шумные голоса — как настроено",
                          diarize.auto_limit([0.68, 0.71, 0.78], {}) == 0.78)
    failures += not check("порог по записи: мерить не на чем — не выдумываем",
                          diarize.auto_limit([0.9], {}) is None)
    halves = diarize.halves([diarize.SpeakerSpan(i, i + 2, 0) for i in range(0, 20, 2)])
    failures += not check("речь делится пополам вперемешку",
                          len(halves[0]) == len(halves[1]) == 5
                          and not {id(s) for s in halves[0]} & {id(s) for s in halves[1]})

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
            if src_file.is_dir():          # папка с приложенными документами
                continue
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

            # --- корзина: удалённое лежит рядом и возвращается ---
            waste = library.trash(out_dir)
            failures += not check("удалённое попало в корзину", len(waste) == 1,
                                  ", ".join(w["title"] for w in waste))
            failures += not check("в корзине видно, сколько осталось дней",
                                  waste and waste[0]["days_left"] == 30,
                                  str(waste[0]["days_left"]) if waste else "")
            back = library.restore(out_dir, waste[0]["id"])
            titles = [i["id"] for i in library.entries(out_dir)]
            failures += not check("запись возвращается из корзины",
                                  back.get("ok") and copy["id"] in titles,
                                  f"вернулось файлов: {back.get('restored')}")
            failures += not check("после возврата корзина пуста",
                                  not library.trash(out_dir))

            library.delete(out_dir, copy["id"])
            waste = library.trash(out_dir)
            # Срок вышел — выметаем сами, не дожидаясь человека.
            box = Path(out_dir) / library.TRASH_NAME / waste[0]["id"]
            meta = json.loads((box / library.TRASH_META).read_text("utf-8"))
            meta["deleted_at"] = time.time() - 31 * 86400
            (box / library.TRASH_META).write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
            failures += not check("пролежавшее месяц стирается само",
                                  library.sweep(out_dir, 30) == 1
                                  and not library.trash(out_dir))
            failures += not check("после корзины запись не воскресает в архиве",
                                  copy["id"] not in [i["id"] for i in library.entries(out_dir)])

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

    # --- голоса по ходу разговора: на настоящем звуке ---
    # Берём два самых длинных куска разных голосов из тестовой записи и
    # скармливаем их так, как это делает запись созвона.
    voiced = diarize.diarize(str(wav), settings)
    by_voice: dict[int, list] = {}
    for span in voiced:
        by_voice.setdefault(span.speaker, []).append(span)
    picked = sorted(by_voice, key=lambda k: -sum(s.end - s.start for s in by_voice[k]))[:2]
    live = record.Stenographer(settings)
    live.session = record.Session(id="live", started_at=time.time(),
                                  directory=Path("/tmp/selftest-rec"), title="Живая",
                                  mode="room")
    whole = media.read_wav(wav)
    fresh = []
    for number, voice in enumerate(picked):
        for span in [s for s in by_voice[voice] if s.end - s.start >= 1.5][:2]:
            fresh.append(record.Line(span.start, span.end, "room", f"речь {number}"))
    live.session.lines = list(fresh)
    live._live_voices(fresh, whole, whole[:0], 0.0, room=True)
    named = [line for line in fresh if line.voice]
    failures += not check("голоса получают номер прямо во время записи",
                          len(named) >= 2 and all(line.speaker for line in named),
                          ", ".join(f"{line.voice}:{line.speaker}" for line in fresh))
    # Сколько голосов найдётся на коротких репликах — вопрос к самой записи,
    # а не к механизму. Проверяем то, что обязано выполняться всегда: один и
    # тот же кусок речи должен опознаться как тот же голос.
    twice = [record.Line(fresh[0].start, fresh[0].end, "room", "повтор")]
    live.session.lines += twice
    live._live_voices(twice, whole, whole[:0], 0.0, room=True)
    failures += not check("тот же голос узнаётся снова, а не заводится заново",
                          twice[0].voice == fresh[0].voice,
                          f"{fresh[0].voice} → {twice[0].voice}")
    failures += not check("голосов не больше разрешённого",
                          len({line.voice for line in live.session.lines if line.voice})
                          <= int(settings.get("live_voice_limit", 9)))
    if named:
        key = named[0].voice
        live.rename_voice(key, "Ирина")
        failures += not check("имя голоса расходится по всем его репликам",
                              all(line.speaker == "Ирина" and line.tagged
                                  for line in fresh if line.voice == key))
        live.rename_voice(key, "")
        failures += not check("имя можно снять — остаётся номер",
                              all(not line.tagged and line.speaker.startswith("Спикер")
                                  for line in fresh if line.voice == key),
                              next(line.speaker for line in fresh if line.voice == key))

    # --- голоса, которые оказались одним человеком ---
    # Два чипа с одним именем — это один человек, и сказал это не алгоритм, а
    # тот, кто их назвал. Проверяем на выдуманных отпечатках: важно правило, а
    # не звук.
    folder = record.Stenographer(settings)
    folder.session = record.Session(id="fold", started_at=time.time(),
                                    directory=Path("/tmp/selftest-rec"),
                                    title="Свод", mode="call")
    import numpy as np

    def _vector(seed: int):
        v = np.random.default_rng(seed).normal(size=192).astype("float32")
        return v / np.linalg.norm(v)

    folder.session.voices = {f"V{i}": [_vector(i)] for i in (1, 2, 3)}
    folder.session.voice_names = {f"V{i}": f"Собеседник {i}" for i in (1, 2, 3)}
    folder.session.lines = [record.Line(i * 2, i * 2 + 2, "them", "речь") for i in range(3)]
    for line, key in zip(folder.session.lines, ("V1", "V2", "V3")):
        line.voice, line.speaker = key, folder.session.voice_names[key]
    folder.rename_voice("V1", "Венера")
    folder.rename_voice("V3", " венера ")
    failures += not check("два чипа с одним именем сводятся в один голос",
                          len(folder.session.voices) == 2,
                          ", ".join(folder.session.voice_names.values()))
    failures += not check("реплики слитого голоса подписаны одинаково",
                          {line.speaker for line in folder.session.lines
                           if line.voice == "V1"} == {"Венера"},
                          str([line.speaker for line in folder.session.lines]))
    failures += not check("номера безымянных идут подряд",
                          folder.session.voice_names.get("V2") == "Собеседник 1",
                          folder.session.voice_names.get("V2", ""))
    same = [_vector(7), _vector(7) * 0.9 + _vector(8) * 0.1]
    folder.session.voices = {"V1": [same[0]], "V2": [same[1] / np.linalg.norm(same[1])]}
    folder.session.voice_names = {"V1": "Собеседник 1", "V2": "Собеседник 2"}
    folder.session.named = set()
    folder._fold_voices()
    failures += not check("похожие безымянные голоса тоже сводятся",
                          len(folder.session.voices) == 1,
                          ", ".join(folder.session.voices))

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

    print("\n13. Сроки словами превращаются в даты")
    from datetime import date as _date
    base = _date(2026, 8, 27)          # четверг
    for said, want in [("завтра", "28 августа"), ("послезавтра", "29 августа"),
                       ("до пятницы", "28 августа"), ("к понедельнику", "31 августа"),
                       ("к концу месяца", "31 августа"),
                       ("на следующей неделе", "31 августа")]:
        got = dates.resolve(said, base)
        failures += not check(f"«{said}»", want in got, got)
    failures += not check("названную дату второй раз не пишем",
                          dates.resolve("3 сентября", base) == "3 сентября")
    failures += not check("прочерк остаётся прочерком",
                          dates.resolve("—", base) == "—")

    table = ("## Задачи\n| Задача | Кто | Срок |\n|---|---|---|\n"
             "| Макеты | Ира | завтра |\n| Смета | Дима | — |\n\n"
             "В тексте до пятницы дату не ставим.")
    fixed = dates.process(table, base)
    failures += not check("дата попала в колонку срока",
                          "завтра (28 августа)" in fixed)
    failures += not check("связный текст не тронут",
                          "В тексте до пятницы дату не ставим." in fixed)
    failures += not check("без даты записи ничего не меняется",
                          dates.process(table, None) == table)

    print("\n14. Проверка «что упущено»")
    doc = ("## Краткое саммари\n- Обсудили релиз.\n\n## Решения\n- Режем онбординг.")

    class FakeModel:
        def __init__(self, answer):
            self.answer = answer

        def chat(self, system, prompt):
            return self.answer

    found = summarize.missed_items(
        FakeModel("- Решения | Скидка 13% при 10 000 XP\nмусор без разделителя"),
        presets.MEETING, "расшифровка", doc)
    failures += not check("разобран только осмысленный ответ", len(found) == 1,
                          str(found))
    # Модель любит процитировать кусок документа вместо списка недостающего.
    failures += not check(
        "кусок таблицы за ответ не принимаем",
        summarize.missed_items(
            FakeModel("| Задача | Кто | Срок |\n|---|---|---|\n| Тексты | Маркетинг | — |"),
            presets.MEETING, "t", doc) == [])
    failures += not check(
        "строка без названия раздела отбрасывается",
        summarize.missed_items(FakeModel(" | что-то важное"), presets.MEETING, "t", doc) == [])
    grown = summarize.add_missed(doc, presets.MEETING, found)
    failures += not check("дописано в нужный раздел",
                          grown.index("Скидка 13%") > grown.index("## Решения"))
    failures += not check("прежний текст на месте", "Режем онбординг." in grown)
    failures += not check("«всё на месте» — ничего не трогаем",
                          summarize.missed_items(FakeModel("ВСЁ НА МЕСТЕ"),
                                                 presets.MEETING, "t", doc) == [])
    failures += not check("чужой раздел отбрасывается",
                          summarize.add_missed(doc, presets.MEETING,
                                               [("Небывалый раздел", "мусор")]) == doc)

    print("\n15. Ошибки на плохом входе")
    bad = runner.submit("/tmp/нет-такого-файла.mp4")
    for _ in range(40):
        if bad.status not in ("pending", "running"):
            break
        time.sleep(0.1)
    failures += not check("несуществующий файл", bad.status == "error", bad.error[:60])

    print("\n15b. Выдуманные титры и отставшая дорожка")
    from app.asr import _is_hallucination
    failures += not check("«Продолжение следует» отсеивается",
                          _is_hallucination("Продолжение следует..."))
    failures += not check("«Субтитры сделал…» отсеивается",
                          _is_hallucination("Субтитры сделал DimaTorzok"))
    failures += not check("настоящая речь остаётся",
                          not _is_hallucination("Давайте пробежимся по статусам."))
    failures += not check("длинная фраза со «спасибо» остаётся",
                          not _is_hallucination(
                              "Спасибо за внимание — вопросы разберём в конце доклада"))

    # Whisper склеивает выдумку и настоящую фразу в один сегмент — выбрасывать
    # такое целиком нельзя, надо срезать только начало.
    words, moment = [], 0.0
    for word in ["Продолжение", "следует...", "Давайте", "пробежимся", "по", "статусам"]:
        words.append(asr.Word(moment, moment + 0.3, (" " if words else "") + word))
        moment += 0.35
    glued = asr.Segment(0.0, moment, "Продолжение следует... Давайте пробежимся по статусам",
                        words)
    kept = asr._cleanup([glued])
    failures += not check("приклеенные титры срезаются, речь остаётся",
                          len(kept) == 1 and kept[0].text.startswith("Давайте")
                          and len(kept[0].words) == 4,
                          f"{kept[0].text!r}, слов {len(kept[0].words)}" if kept else "пусто")
    failures += not check("таймкод сдвинулся на первое настоящее слово",
                          kept and kept[0].start > 0.5, f"{kept[0].start:.2f} с" if kept else "")

    # Дорожка может встать: не выдано разрешение на микрофон или человек весь
    # созвон молчит. Разбор при этом должен идти по живой дорожке.
    tracks = Path("/tmp/selftest-tracks")
    tracks.mkdir(exist_ok=True)
    mic_file, sys_file = tracks / "mic.pcm", tracks / "sys.pcm"
    mic_file.write_bytes(b"\0" * record.BYTES_PER_SECOND * 2)
    sys_file.write_bytes(b"\0" * record.BYTES_PER_SECOND * 40)
    steno.session.mode = "call"
    steno.session.stalled = ""
    ready = steno._ready(mic_file, sys_file)
    failures += not check("отставшая дорожка не держит расшифровку",
                          ready > record.BYTES_PER_SECOND * 30,
                          f"{ready // record.BYTES_PER_SECOND} с из 40")
    failures += not check("про молчащую дорожку сказано один раз",
                          steno.session.stalled == "mic")
    sys_file.write_bytes(b"\0" * record.BYTES_PER_SECOND * 3)
    failures += not check("пока дорожки идут вровень — ждём обе",
                          steno._ready(mic_file, sys_file)
                          == record.BYTES_PER_SECOND * 2)

    print("\n16. Английский язык")
    # Проверяем не перевод как таковой, а то, что язык доходит до всех мест,
    # где рождается текст: профили, документ, суммы, даты, подписи спикеров.
    english = Settings({**settings, "ui_language": "en", "doc_language": "en"})
    failures += not check("профиль на английском",
                          presets.resolve(english).sections[0][1] == "Summary",
                          presets.resolve(english).sections[0][1])
    failures += not check("свои правила по-английски",
                          "In short" in presets.custom_example("en"))
    failures += not check("ключи разделов не зависят от языка",
                          [k for k, _ in presets.builtin("en")["meeting"].sections]
                          == [k for k, _ in presets.builtin("ru")["meeting"].sections])
    en_estimate = compute.process(
        "| Job | Quantity | Rate | Amount |\n|---|---|---|---|\n| Demo | 4 | 25 | |\n",
        "Estimate", "en")
    failures += not check("«Total» дописано по-английски",
                          "**Total**" in en_estimate.markdown
                          and en_estimate.tables[0].total == 100)
    en_dates = dates.process(
        "| Task | Deadline |\n|---|---|\n| Ship it | tomorrow |\n",
        _date(2026, 8, 27), "en")
    failures += not check("«tomorrow» стало датой", "(August 28)" in en_dates, en_dates.strip()[-40:])
    failures += not check("подпись спикера по-английски",
                          i18n.d("speaker", "en", n=2) == "Speaker 2")
    en_turns = [merge.Turn(start=0, end=2, speaker=0, text="hello there")]
    en_doc = render.transcript_markdown(en_turns, {"title": "Call"}, None, "en")
    failures += not check("транскрипт по-английски",
                          "# Transcript — Call" in en_doc and "| Duration |" in en_doc)
    failures += not check("папка результатов по языку",
                          settings_output_name(english) == "Transcripts"
                          and settings_output_name(
                              Settings({**settings, "doc_language": "ru"}))
                          == "Расшифровка записей")
    failures += not check("сообщения переводятся",
                          i18n.t("state.done", "en") == "Done"
                          and i18n.t("state.done", "ru") == "Готово")
    missing = [key for key, row in {**i18n.MESSAGES, **i18n.DOCUMENT}.items()
               if not row.get("ru") or not row.get("en")]
    failures += not check("перевод полный", not missing, ", ".join(missing[:5]))

    print("\n17. Знакомые голоса")
    # Память между записями: отпечаток снимается с уже разобранной записи, где
    # человек проверил имена. Хранилище на время теста — во временной папке,
    # чтобы не трогать настоящее.
    import numpy as np

    from app import voices

    voices.STORE, real_store = Path("/tmp/selftest-voices.json"), voices.STORE
    voices.STORE.unlink(missing_ok=True)
    result_file = job.files["result"]
    # В записи подписан один человек, остальные остались «Спикер N».
    labels = {k: v.get("label") for k, v in
              json.loads(Path(result_file).read_text("utf-8"))["speakers"].items()}
    named = [n for n in labels.values() if not voices._is_placeholder(n)]
    learned = voices.learn(result_file, int(settings["num_threads"]))
    failures += not check("голоса запомнились по команде", learned.get("ok"),
                          str(learned.get("error")))
    failures += not check("запомнен только названный человек",
                          list(learned.get("learned", {})) == named,
                          f"{list(learned.get('learned', {}))} при именах {labels}")
    person = named[0]
    failures += not check("«Спикер 2» именем не считается",
                          voices._is_placeholder("Спикер 2")
                          and voices._is_placeholder("Speaker 3")
                          and not voices._is_placeholder("Анна Петрова"))
    stored = voices.load().get(person, [])
    failures += not check("отпечатков несколько", len(stored) >= 2, f"{len(stored)}")
    failures += not check("свой отпечаток узнаётся",
                          voices.match(stored[0])[0] == person,
                          f"{voices.match(stored[0])}")
    stranger = np.random.default_rng(7).normal(size=stored[0].shape).astype("float32")
    stranger /= float(np.linalg.norm(stranger))
    failures += not check("чужой голос не подписывается именем",
                          voices.match(stranger)[0] == "", f"{voices.match(stranger)}")
    voices.remember("Двойник", [stored[0]])
    failures += not check("двое похожих — не называем никого",
                          voices.match(stored[0])[0] == "",
                          f"{voices.match(stored[0])}")
    failures += not check("голос забывается", voices.forget("Двойник")
                          and [v["name"] for v in voices.names()] == [person])

    # Ради этого всё и затевалось: в следующей записи человек подписывается сам.
    result_data = json.loads(Path(result_file).read_text("utf-8"))
    heard = [diarize.SpeakerSpan(float(t["start"]), float(t["end"]),
                                 int(str(t["speaker"])[1:]) - 1)
             for t in result_data["turns"] if str(t.get("speaker") or "").startswith("S")]
    found = voices.identify(media.read_wav(wav), heard, int(settings["num_threads"]))
    failures += not check("знакомый голос узнан в записи",
                          list(found.values()) == [person], f"{found}")
    # Запись, где имена не проставлены, запоминанию не подлежит — иначе в
    # памяти окажется «Спикер 1» с чужим голосом.
    anonymous = Path("/tmp/selftest-anon.result.json")
    raw = json.loads(Path(result_file).read_text("utf-8"))
    raw["speakers"] = {k: {**v, "label": f"Спикер {i + 1}"}
                       for i, (k, v) in enumerate(raw["speakers"].items())}
    anonymous.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
    failures += not check("без имён запоминать нечего",
                          voices.learn(anonymous).get("error") == "no-names")
    anonymous.unlink(missing_ok=True)

    # Ради чего всё: следующая запись того же человека подписана его именем
    # сама, без единого клика — и в карточке, и в файлах.
    settings["output_dir"] = "/tmp/selftest-known"
    again = pipeline.Runner(settings).submit(src)
    while again.status in ("pending", "running"):
        time.sleep(0.2)
    labels = [v["label"] for v in again.speakers.values()]
    failures += not check("новая запись подписана именем сама", person in labels,
                          ", ".join(labels) or again.error)
    failures += not check("имя дошло до файлов", again.files
                          and person in Path(again.files["transcript_md"]).read_text("utf-8"))
    settings["output_dir"] = "/tmp/selftest-out"
    shutil.rmtree("/tmp/selftest-known", ignore_errors=True)

    voices.STORE.unlink(missing_ok=True)
    voices.STORE = real_store

    print("\n21. Метки на записи")
    # Метка должна вести туда, где это действительно прозвучало, — поэтому
    # время берётся из расшифровки, а не у модели.
    from app import marks as marks_module

    spoken = [
        merge.Turn(start=0, end=12, speaker=0,
                   text="Давайте начнём с релиза, у нас двадцать восьмое"),
        merge.Turn(start=45, end=70, speaker=1,
                   text="Макеты онбординга подготовит Ирина к двадцать восьмому августа"),
        merge.Turn(start=120, end=150, speaker=0,
                   text="Смету подрядчика согласуем к первому сентября"),
        merge.Turn(start=200, end=230, speaker=1,
                   text="Отпуск разработчика с пятого сентября в план не заложен"),
    ]
    made = marks_module.build(spoken, {
        "tasks": "| Задача | Ответственный | Срок |\n|---|---|---|\n"
                 "| Подготовить макеты онбординга | Ирина | 28 августа |",
        "risks": "- Отпуск разработчика с 5 сентября не заложен в план.\n"
                 "- Про полёт на Марс ничего не решили.",
    }, [{"at": 300, "text": "- Обсудили сроки релиза"}], "ru")
    by_kind = {m["kind"]: m for m in made}
    failures += not check("метка встала на нужную реплику",
                          by_kind.get("tasks", {}).get("at") == 45.0,
                          str(by_kind.get("tasks")))
    failures += not check("риск нашёлся по своим словам",
                          by_kind.get("risks", {}).get("at") == 200.0,
                          str(by_kind.get("risks")))
    failures += not check("выдуманного в записи пункта метка не получает",
                          not any("Марс" in m["text"] for m in made),
                          ", ".join(m["text"][:20] for m in made))
    failures += not check("заметка по ходу метится своим временем",
                          by_kind.get("note", {}).get("at") == 300.0)
    failures += not check("метки идут по времени",
                          [m["at"] for m in made] == sorted(m["at"] for m in made))
    vtt = marks_module.to_vtt(made, "ru")
    failures += not check("главы выгружаются в WebVTT",
                          vtt.startswith("WEBVTT") and "00:00:45.000 -->" in vtt
                          and "Задача: Подготовить макеты" in vtt,
                          vtt.splitlines()[3] if len(vtt.splitlines()) > 3 else "")
    failures += not check("метки записаны в файлы записи",
                          "chapters" in job.files
                          and Path(job.files["chapters"]).read_text("utf-8").startswith("WEBVTT")
                          if job.marks else True,
                          f"меток у записи: {len(job.marks)}")
    # Документ и расшифровка почти никогда не совпадают дословно: в решении
    # «остановлено», в реплике «остановить». По целым словам метка терялась
    # ровно там, где человек её ждёт.
    formed = marks_module.build(
        [merge.Turn(start=28, end=39, speaker=0, text="Кто на связи, я буду"),
         merge.Turn(start=40, end=43, speaker=0, text="Остановить")],
        {"decisions": "- Обсуждение остановлено."}, [], "ru")
    failures += not check("метка находит слово в другой форме",
                          [m["at"] for m in formed] == [40.0],
                          str(formed))

    print("\n22. Целость записи экрана")
    # Помощник дописывает оглавление mp4 последним действием. Если запись
    # оборвалась, кадры остаются, оглавления нет — и такой файл не открывает
    # ни один плеер, хотя весит мегабайты. Раньше он молча попадал в карточку.
    broken = Path("/tmp/selftest-broken.mp4")
    broken.write_bytes(bytes.fromhex("0000001c667479706d70343200000001")
                       + b"isommp41mp42" + bytes.fromhex("00000008")
                       + b"wide" + bytes.fromhex("00000000") + b"mdat" + b"\0" * 512)
    whole = Path("/tmp/selftest-whole.mp4")
    whole.write_bytes(bytes.fromhex("0000001c667479706d70343200000001")
                      + b"isommp41mp42" + (16).to_bytes(4, "big") + b"mdat" + b"\0" * 8
                      + (16).to_bytes(4, "big") + b"moov" + b"\0" * 8)
    failures += not check("недописанный mp4 распознаётся как битый",
                          not media.playable_mp4(broken))
    failures += not check("дописанный mp4 признаётся годным",
                          media.playable_mp4(whole))
    failures += not check("несуществующего файла проверка не роняет",
                          not media.playable_mp4(Path("/tmp/нет-такого.mp4")))
    shot = Path(job.files["result"]).with_suffix("")
    stem_dir, stem_name = shot.parent, shot.name.replace(".result", "")
    (stem_dir / f"{stem_name}.mp4").write_bytes(broken.read_bytes())
    failures += not check("битая запись экрана в карточку не попадает",
                          "video" not in library.files_of(stem_dir, stem_name),
                          str(library.files_of(stem_dir, stem_name).get("video")))
    (stem_dir / f"{stem_name}.mp4").unlink(missing_ok=True)
    broken.unlink(missing_ok=True)
    whole.unlink(missing_ok=True)

    print("\n19. Документы к записи")
    # Половина задач на созвоне ссылается на документ, который живёт отдельно.
    # Проверяем весь путь: приложить → достать текст → отдать модели → убрать.
    from app import attach

    result_file = Path(job.files["result"])
    papers = Path("/tmp/selftest-docs")
    papers.mkdir(exist_ok=True)
    (papers / "смета.md").write_text(
        "# Смета на онбординг\n\nДемонтаж перегородки — 12 часов по 3 000 ₽.\n", "utf-8")
    # docx — это zip с xml; собираем настоящий, а не подделку с расширением.
    import zipfile

    docx = papers / "техзадание.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml",
                    "<w:document><w:body>"
                    "<w:p><w:r><w:t>Требование 4.2: онбординг из трёх экранов.</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>Срок приёмки — 15 сентября.</w:t></w:r></w:p>"
                    "</w:body></w:document>")
    added = attach.add(result_file, [str(papers / "смета.md"), str(docx)])
    failures += not check("документы приложились", added.get("ok")
                          and len(added.get("added", [])) == 2,
                          ", ".join(added.get("added", [])))
    listed = attach.items(result_file)
    failures += not check("оба документа видны в карточке", len(listed) == 2
                          and all(item["readable"] for item in listed),
                          ", ".join(f"{i['name']}:{i['readable']}" for i in listed))
    text = attach.context(result_file)
    failures += not check("текст достаётся и из markdown, и из docx",
                          "Демонтаж перегородки" in text and "Требование 4.2" in text,
                          text[:60].replace("\n", " "))
    failures += not check("каждый документ подписан именем",
                          "### смета.md" in text and "### техзадание.docx" in text)

    # Тот же документ приложили второй раз — первая версия не затирается.
    attach.add(result_file, [str(papers / "смета.md")])
    failures += not check("одинаковые имена не затирают друг друга",
                          len(attach.items(result_file)) == 3,
                          ", ".join(i["name"] for i in attach.items(result_file)))

    # Модель должна увидеть документы в задании на саммари.
    FakeOllama.calls.clear()
    fresh_job = runner.resummarize(str(result_file))
    while fresh_job.status in ("pending", "running"):
        time.sleep(0.2)
    failures += not check("саммари пересобирается по команде",
                          fresh_job.status == "done", fresh_job.error)
    failures += not check("документы дошли до модели",
                          any("Требование 4.2" in call for call in FakeOllama.calls),
                          f"запросов: {len(FakeOllama.calls)}")
    failures += not check("имена документов попали в шапку задания",
                          any("смета.md" in call for call in FakeOllama.calls))
    failures += not check("файлы записи переписаны",
                          Path(fresh_job.files["summary"]).exists())

    attach.remove(result_file, "смета.md")
    failures += not check("документ убирается",
                          [i["name"] for i in attach.items(result_file)]
                          == ["смета (2).md", "техзадание.docx"],
                          ", ".join(i["name"] for i in attach.items(result_file)))
    shutil.rmtree(papers, ignore_errors=True)
    shutil.rmtree(attach.folder_for(result_file), ignore_errors=True)

    print("\n20. Справочник людей и команд")
    # Список участников созвона каждый раз набирался заново, хотя люди те же.
    from app import people

    people.STORE, real_people = Path("/tmp/selftest-people.json"), people.STORE
    people.STORE.unlink(missing_ok=True)
    people.add("Ирина Волкова", "Подрядчик", "прораб")
    people.add("Сергей Ким", "Подрядчик")
    people.add("Дмитрий", "Наш продукт", "дизайн")
    failures += not check("люди заводятся и сортируются по командам",
                          [p["name"] for p in people.items(with_voices=False)]
                          == ["Дмитрий", "Ирина Волкова", "Сергей Ким"],
                          ", ".join(p["name"] for p in people.items(with_voices=False)))
    people.add("Ирина Волкова", role="руководитель работ")
    same = next(p for p in people.load() if p["name"] == "Ирина Волкова")
    failures += not check("повторное добавление уточняет, а не плодит двойников",
                          len(people.load()) == 3 and same["org"] == "Подрядчик"
                          and same["role"] == "руководитель работ",
                          f"{len(people.load())} человек, {same}")
    failures += not check("команда отдаёт всех своих участников",
                          people.of_org("подрядчик") == ["Ирина Волкова", "Сергей Ким"],
                          ", ".join(people.of_org("подрядчик")))
    groups = people.orgs()
    failures += not check("команды собираются для подстановки на созвоне",
                          [g["org"] for g in groups] == ["Наш продукт", "Подрядчик"],
                          ", ".join(g["org"] for g in groups))
    failures += not check("подпись человека знает его команду",
                          people.describe("Дмитрий") == "Дмитрий · Наш продукт",
                          people.describe("Дмитрий"))

    # Голос и человек связаны именем: справочник показывает, чей голос запомнен.
    voices.STORE, keep_store = Path("/tmp/selftest-voices2.json"), voices.STORE
    voices.STORE.unlink(missing_ok=True)
    voices.remember("Дмитрий", [_vector(3)])
    marked = {p["name"]: p["voice"] for p in people.items()}
    failures += not check("в справочнике видно, чей голос уже запомнен",
                          marked.get("Дмитрий") and not marked.get("Сергей Ким"),
                          str(marked))
    voices.STORE.unlink(missing_ok=True)
    voices.STORE = keep_store

    failures += not check("человек убирается из справочника",
                          people.remove("Сергей Ким") and len(people.load()) == 2)
    people.STORE.unlink(missing_ok=True)
    people.STORE = real_people

    print("\n18. Очередь разбора: новый созвон важнее")
    # Разбор прошлой записи не должен запирать микрофон: пока идёт разговор,
    # он ждёт, а после — доделывается сам. Проверяем правило, а не звук.
    queue = record.Stenographer(settings)
    first = record.Session(id="a", started_at=time.time(),
                           directory=Path("/tmp/selftest-rec"), title="Первый")
    second = record.Session(id="b", started_at=time.time(),
                            directory=Path("/tmp/selftest-rec"), title="Второй")
    queue.session = first
    failures += not check("во время записи начать вторую нельзя", queue.is_active())
    first.state = "queued"
    queue.queue.append(first)
    failures += not check("пока прошлая разбирается, запись начать можно",
                          not queue.is_active())
    queue.session = second          # человек начал новый созвон
    held = threading.Event()
    threading.Thread(target=lambda: (queue._hold(first), held.set()), daemon=True).start()
    time.sleep(0.5)
    failures += not check("разбор встал на паузу, пока идёт запись",
                          not held.is_set() and first.message == i18n.t("rec.paused"),
                          first.message)
    second.state = "queued"         # запись закончилась
    failures += not check("после записи разбор продолжается",
                          held.wait(6) and first.message == i18n.t("rec.resumed"),
                          first.message)
    queue.session = second
    queue.queue.clear()
    snapshot = queue.snapshot_of(second)
    queue.queue.append(first)
    snapshot = queue.snapshot_of(second)
    failures += not check("окно видит очередь разбора",
                          [q["title"] for q in snapshot["queue"]] == ["Первый"],
                          str(snapshot["queue"]))


    asr.transcribe = real
    server.shutdown()
    print(f"\n{'ВСЁ ХОРОШО' if not failures else f'ПРОБЛЕМ: {failures}'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
