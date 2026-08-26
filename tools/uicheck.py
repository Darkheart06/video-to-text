"""Проверка интерфейса без macOS: открываем index.html в headless-браузере
с поддельным мостом pywebview и снимаем скриншоты.

Запуск: python tools/uicheck.py [папка_для_скриншотов]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui" / "index.html"
sys.path.insert(0, str(ROOT))

SUMMARY_SECTIONS = {
    "summary": "- Согласовали объём первого релиза и перенесли две задачи в следующий спринт.\n"
               "- Бюджет на подрядчика утверждён в размере **740 000 ₽**.\n"
               "- Не закрыт вопрос по интеграции с 1С — нужен ответ от финансов.",
    "brief": "### Контекст\nЕженедельная встреча команды продукта по подготовке релиза 2.4.\n\n"
             "### Обсуждённые темы\n- Объём релиза и что переносится\n- Бюджет подрядчика\n"
             "- Интеграция с 1С\n\n### Ключевые тезисы\n- Онбординг режем до трёх экранов.\n"
             "- Сроки держим за счёт сокращения объёма, а не за счёт качества.",
    "tasks": "| Задача | Ответственный | Срок |\n|---|---|---|\n"
             "| Подготовить макеты онбординга | Ирина | 28 августа |\n"
             "| Согласовать смету с подрядчиком | Дмитрий | 1 сентября |\n"
             "| Уточнить требования к 1С | Сергей | — |",
    "decisions": "- Онбординг сокращаем до трёх экранов.\n- Подрядчика утверждаем, смета до 740 000 ₽.",
    "risks": "- Нет ответа от финансов по 1С — блокирует оценку сроков.\n"
             "- Отпуск разработчика с 5 сентября не заложен в план.",
}

ESTIMATE_SECTIONS = {
    "works": "| Работа | Количество | Единица | Ставка | Стоимость |\n|---|---|---|---|---|\n"
             "| Демонтаж перегородки | 12 | час | 3 000 | 36 000 ₽ |\n"
             "| Штробление стен | 8 | час | 2 500 | 20 000 ₽ |\n"
             "| **Итого** |  |  |  | **56 000 ₽** |",
    "terms": "- Оплата по факту приёмки.",
    "open": "- Про двери цену не назвали.",
    "summary": "- Смета на черновые работы в двухкомнатной квартире.",
}

ESTIMATE_TABS = [["works", "Работы"], ["terms", "Условия"],
                 ["open", "Что уточнить"], ["summary", "Кратко"]]

TURNS = [
    (12.4, 41.8, "S1", "Давайте начнём с релиза. У нас по плану двадцать восьмое, "
                       "и я хочу понять, что реально успеваем."),
    (42.1, 58.0, "S2", "Успеваем всё, кроме онбординга. Там три экрана из пяти готовы."),
    (58.4, 96.2, "S1", "Тогда режем до трёх экранов и переносим остальное. "
                       "Ирина, макеты к двадцать восьмому сделаешь?"),
    (96.5, 104.0, "S3", "Сделаю, но мне нужны финальные тексты до среды."),
    (104.2, 151.9, "S2", "Тексты будут. Ещё вопрос по подрядчику — смета пришла "
                         "на семьсот сорок тысяч, надо утверждать."),
]

JOB = {
    "id": "demo1234", "source": "/Users/sergey/Movies/Встреча команды 25.08.mp4",
    "title": "Встреча команды 25.08.mp4", "status": "done", "stage": "done",
    "message": "Готово", "progress": 1.0, "error": "",
    "files": {
        "summary": "/Users/sergey/output/Встреча команды 25.08.summary.md",
        "transcript_md": "/Users/sergey/output/Встреча команды 25.08.transcript.md",
        "subtitles": "/Users/sergey/output/Встреча команды 25.08.subtitles.srt",
        "result": "/Users/sergey/output/Встреча команды 25.08.result.json",
    },
    "meta": {"duration": 3187, "language": "ru"},
    "summary_md": "## Краткое саммари\n" + SUMMARY_SECTIONS["summary"],
    "summary_sections": SUMMARY_SECTIONS,
    "transcript_md": "…",
    "speakers": {
        "S1": {"label": "Спикер 1", "seconds": 1284.0},
        "S2": {"label": "Спикер 2", "seconds": 902.5},
        "S3": {"label": "Спикер 3", "seconds": 431.2},
    },
    "warnings": [],
    "turns": [{"start": s, "end": e, "speaker": spk, "text": t} for s, e, spk, t in TURNS],
}

ESTIMATE_JOB = {
    "id": "demo9999", "source": "/Users/sergey/Movies/Смета по ремонту.m4a",
    "title": "Смета по ремонту.m4a", "status": "done", "stage": "done",
    "message": "Готово", "progress": 1.0, "error": "",
    "files": {"summary": "/Users/sergey/output/Смета по ремонту.summary.md",
              "tables": "/Users/sergey/output/Смета по ремонту.таблицы.csv"},
    "meta": {"duration": 214, "language": "ru"},
    "summary_md": "## Работы\n" + ESTIMATE_SECTIONS["works"],
    "summary_sections": ESTIMATE_SECTIONS,
    "summary_tabs": ESTIMATE_TABS,
    "preset": "estimate",
    "transcript_md": "…", "speakers": {}, "warnings": [],
    "turns": [{"start": 3.0, "end": 9.0, "speaker": "S1",
               "text": "Значит, демонтаж перегородки — двенадцать часов по три тысячи."}],
}

RUNNING = {
    **JOB, "id": "demo5678", "title": "Интервью с клиентом.mov", "status": "running",
    "stage": "asr", "message": "Распознавание, часть 2 из 6", "progress": 0.34,
    "files": {}, "summary_sections": {}, "turns": [], "speakers": {},
    "warnings": ["Разделение по спикерам не выполнено: модели ещё не скачаны"],
}

REC = {
    "id": "rec1", "state": "recording", "title": "Созвон 25.08 21-40",
    "duration": 372.0, "message": "Записано 6 мин, реплик 14", "error": "",
    "files": {}, "summary_md": "", "summary_sections": {}, "line_count": 3,
    "notes": [{"at": 300, "text": "- Обсудили сроки релиза\n- Ирина берёт макеты"}],
    "lines": [
        {"start": 12.0, "who": "me", "label": "Я", "text": "Давайте начнём с релиза."},
        {"start": 19.5, "who": "them", "label": "Собеседник",
         "text": "Успеваем всё, кроме онбординга."},
        {"start": 31.0, "who": "me", "label": "Я", "text": "Тогда режем до трёх экранов."},
    ],
}

LIB = [
    {"id": "lib1", "path": "/Users/sergey/output/Созвон 26.08 07-30.result.json",
     "title": "Созвон 26.08 07-30", "kind": "call", "at": 1787725796.0,
     "when": "2026-08-26 07:31", "duration": 671.0, "language": "ru",
     "speakers": 2, "lines": 42, "preset": "meeting",
     "preview": "Согласовали объём первого релиза и перенесли две задачи."},
    {"id": "lib2", "path": "/Users/sergey/output/Смета по ремонту.result.json",
     "title": "Смета по ремонту", "kind": "file", "at": 1787700000.0,
     "when": "2026-08-26 00:28", "duration": 214.0, "language": "ru",
     "speakers": 0, "lines": 6, "preset": "estimate",
     "preview": "Демонтаж перегородки, штробление стен, вывоз мусора."},
    {"id": "lib3", "path": "/Users/sergey/output/Встреча команды 25.08.result.json",
     "title": "Встреча команды 25.08.mp4", "kind": "file", "at": 1787600000.0,
     "when": "2026-08-25 23:49", "duration": 3187.0, "language": "ru",
     "speakers": 3, "lines": 214, "preset": "meeting",
     "preview": "Еженедельная встреча команды продукта по подготовке релиза 2.4."},
]

BRIDGE = """
window.pywebview = {api: {
  get_settings: async () => (%(settings)s),
  environment: async () => (%(env)s),
  save_settings: async v => (%(settings)s),
  choose_files: async () => [],
  choose_output_dir: async () => "/Users/sergey/output",
  choose_gguf_file: async () => "/Users/sergey/Models/gemma-4-12b-Q4_K_M.gguf",
  test_llm: async () => ({ok: true, backend: "Ollama · gemma4:12b-mlx", answer: "готово"}),
  start: async () => [],
  cancel: async () => true,
  reveal: async () => true,
  open_file: async () => true,
  rename_speakers: async () => null,
  prepare_models: async () => ({ok: true}),
  rec_permissions: async () => ({helper: true, screen: true, microphone: true}),
  rec_state: async () => null,
  rec_request: async () => ({screen: true, microphone: true}),
  rec_start: async () => ({ok: true, session: %(rec)s}),
  rec_stop: async () => (%(rec)s),
  rec_cancel: async () => true,
  open_privacy: async () => true,
  presets: async () => (%(presets)s),
  library: async q => ({items: (%(lib)s).filter(
      i => !q || (i.title + " " + i.preview).toLowerCase().includes(q.toLowerCase())),
    dir: "/Users/sergey/output"}),
  library_open: async id => Object.assign({}, %(estimate)s, {id: id, archived: true,
    title: (%(lib)s).find(i => i.id === id).title, message: "Из архива"}),
  library_delete: async () => ({ok: true, removed: 5}),
  library_rename: async () => (%(estimate)s),
  copy: async text => { window.__copied = text; return true; },
}};
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/uicheck")
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "record_autodetect": True, "record_notes_minutes": 5, "record_chunk_seconds": 30,
        "language": "ru", "whisper_model": "large-v3-turbo", "asr_backend": "auto",
        "diarization_enabled": True, "num_speakers": 0, "cluster_threshold": 0.6,
        "summary_enabled": True, "llm_backend": "auto", "llm_num_ctx": 32768,
        "ollama_model": "auto", "ollama_url": "http://127.0.0.1:11434",
        "gguf_path": "", "gguf_gpu_layers": -1,
        "openai_base_url": "http://127.0.0.1:1234/v1", "openai_model": "auto",
        "openai_api_key": "", "output_dir": "", "keep_wav": False,
        "transcript_cleanup": True, "record_dedupe": True,
        "preset": "meeting", "custom_rules": "", "custom_template": "",
        "record_split_speakers": True, "speaker_merge_similarity": 0.78,
        "min_speaker_seconds": 2.0, "min_speaker_share": 0.01,
    }
    env = {
        "ffmpeg": True, "mlx": True, "faster": True, "sherpa": True, "diar_models": True,
        "ollama_models": ["gemma4:12b-mlx", "qwen3:8b"], "ollama_error": "",
        "openai_models": [], "openai_error": "нет соединения",
        "llama_cpp": False, "gguf_path_ok": False, "gguf_size_gb": 0,
        "platform": "Darwin arm64",
        "whisper_models": ["large-v3-turbo", "large-v3", "medium", "small", "base"],
        "output_dir": "/Users/sergey/output",
    }
    from app import presets as presets_mod
    catalogue = {"items": presets_mod.catalogue(), "current": "meeting",
                 "example": presets_mod.CUSTOM_EXAMPLE}
    bridge = BRIDGE % {"settings": json.dumps(settings), "env": json.dumps(env),
                       "rec": json.dumps(REC), "presets": json.dumps(catalogue),
                       "lib": json.dumps(LIB), "estimate": json.dumps(ESTIMATE_JOB)}

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        for scheme in ("light", "dark"):
            page = browser.new_page(viewport={"width": 1180, "height": 820},
                                    color_scheme=scheme)
            # scheme привязываем аргументом: иначе оба обработчика запомнят
            # последнее значение из цикла и подпишут ошибки не той темой.
            page.on("pageerror",
                    lambda e, s=scheme: errors.append(f"{s}: {e}"))
            page.on("console",
                    lambda m, s=scheme: errors.append(f"{s}: {m.text}")
                    if m.type == "error" else None)
            page.add_init_script(bridge)
            page.goto(UI.as_uri())
            page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
            page.wait_for_timeout(250)

            page.screenshot(path=str(out_dir / f"1-empty-{scheme}.png"))

            page.evaluate("(j)=>{state.jobs.set(j.id,j); render();}", JOB)
            page.evaluate("(j)=>{state.jobs.set(j.id,j); render();}", RUNNING)
            page.wait_for_timeout(150)
            page.screenshot(path=str(out_dir / f"2-result-{scheme}.png"), full_page=True)

            page.click('[data-tab="tasks"]')
            page.wait_for_timeout(150)
            page.screenshot(path=str(out_dir / f"3-tasks-{scheme}.png"), full_page=True)

            # --- панель стенографиста ---
            if not page.locator('[data-rec="start"]').count():
                errors.append(f"{scheme}: нет кнопки начала записи")
            page.click('[data-rec="start"]')
            page.wait_for_timeout(250)
            # text_content, а не inner_text: заголовок заметок поднят в верхний
            # регистр средствами CSS, и inner_text вернул бы уже «ЗАМЕТКИ».
            live = page.text_content("#rec") or ""
            for probe in ("Созвон", "Собеседник", "Заметки по ходу", "Ирина"):
                if probe not in live:
                    errors.append(f"{scheme}: в панели записи нет «{probe}»")
            if not page.locator(".rec-dot").count():
                errors.append(f"{scheme}: нет индикатора идущей записи")
            page.screenshot(path=str(out_dir / f"6-record-{scheme}.png"))
            page.click('[data-rec="cancel"]')
            page.wait_for_timeout(150)

            # --- профиль: смета со своими вкладками ---
            page.evaluate("(j)=>{state.jobs.set(j.id,j); render();}", ESTIMATE_JOB)
            page.wait_for_timeout(150)
            est = page.text_content("#jobs") or ""
            for probe in ("Работы", "Что уточнить", "таблицы.csv"):
                if probe not in est:
                    errors.append(f"{scheme}: у сметы нет «{probe}»")
            if "Бриф" in (page.text_content('.job[data-id="demo9999"] .tabs') or ""):
                errors.append(f"{scheme}: у сметы показаны вкладки встречи")
            page.click('.job[data-id="demo9999"] [data-tab="works"]')
            page.wait_for_timeout(120)
            if page.locator("#pane-demo9999 table tbody tr").count() != 3:
                errors.append(f"{scheme}: таблица сметы отрисована неверно")
            if "56 000" not in (page.text_content("#pane-demo9999") or ""):
                errors.append(f"{scheme}: в смете нет итога")
            page.screenshot(path=str(out_dir / f"7-estimate-{scheme}.png"), full_page=True)
            page.evaluate("state.jobs.delete('demo9999'); render();")
            page.wait_for_timeout(100)

            # --- переключатель профиля ---
            if not page.locator("#preset-pick").count():
                errors.append(f"{scheme}: нет выбора профиля")
            page.select_option("#preset-pick", "estimate")
            page.wait_for_timeout(150)
            if "смет" not in (page.text_content("#picker") or "").lower():
                errors.append(f"{scheme}: подсказка профиля не обновилась")

            # --- архив записей ---
            page.wait_for_timeout(200)
            if page.locator(".lib-item").count() != 3:
                errors.append(f"{scheme}: список записей не отрисовался")
            side = page.text_content("#side") or ""
            for probe in ("Созвон 26.08 07-30", "11:11", "созвон", "записей: 3"):
                if probe not in side:
                    errors.append(f"{scheme}: в архиве нет «{probe}»")
            page.fill("#lib-search", "смета")
            page.wait_for_timeout(400)
            if page.locator(".lib-item").count() != 1:
                errors.append(f"{scheme}: поиск по архиву не сработал")
            page.fill("#lib-search", "")
            page.wait_for_timeout(400)
            page.click('[data-lib="lib1"]')
            page.wait_for_timeout(300)
            if not page.locator('.lib-item.active').count():
                errors.append(f"{scheme}: открытая запись не подсвечена")
            if "Созвон 26.08 07-30" not in (page.text_content("#jobs") or ""):
                errors.append(f"{scheme}: запись из архива не открылась")
            # зона перетаскивания скрыта карточками — кнопка выбора файла
            # обязана оставаться на виду
            if not page.locator("#btn-pick-top").is_visible():
                errors.append(f"{scheme}: пропала кнопка выбора записи")
            page.click("#btn-pick-top")
            page.wait_for_timeout(120)
            page.screenshot(path=str(out_dir / f"9-library-{scheme}.png"))
            # удаление в два клика: первый спрашивает, второй убирает
            page.click('[data-libdel="lib3"]')
            page.wait_for_timeout(150)
            if "удалить?" not in (page.text_content("#side") or ""):
                errors.append(f"{scheme}: удаление не переспрашивает")
            page.click('[data-libdel="lib3"]')
            page.wait_for_timeout(300)
            page.click("#btn-side")
            page.wait_for_timeout(150)
            if page.locator("#side").is_visible():
                errors.append(f"{scheme}: архив не скрывается")
            page.click("#btn-side")
            page.wait_for_timeout(150)

            page.click("#btn-settings")
            page.wait_for_timeout(300)
            page.screenshot(path=str(out_dir / f"4-settings-{scheme}.png"))

            # --- подключение языковой модели ---
            page.select_option("#llm-backend", "gguf")
            page.wait_for_timeout(120)
            if page.locator('.llm-block[data-for="openai"]').is_visible():
                errors.append(f"{scheme}: при выборе .gguf показан блок сервера")
            if not page.locator('.llm-block[data-for="gguf"]').is_visible():
                errors.append(f"{scheme}: блок .gguf не показан")
            page.click("#btn-gguf")
            page.wait_for_timeout(150)
            if ".gguf" not in page.input_value("#gguf-path"):
                errors.append(f"{scheme}: путь к модели не подставился")
            page.click("#btn-test-llm")
            page.wait_for_timeout(250)
            if "отвечает" not in page.inner_text("#llm-test-result"):
                errors.append(f"{scheme}: проверка связи не отработала")
            page.locator("#llm-backend").scroll_into_view_if_needed()
            page.wait_for_timeout(120)
            page.screenshot(path=str(out_dir / f"5-llm-{scheme}.png"))

            page.select_option("#llm-backend", "auto")
            page.wait_for_timeout(120)
            if page.locator(".llm-block").count() != 3:
                errors.append(f"{scheme}: в режиме «авто» видны не все способы")

            # --- свои правила ---
            page.locator("#preset-select").scroll_into_view_if_needed()
            if page.locator("#custom-block").is_visible():
                errors.append(f"{scheme}: поля своих правил видны у готового профиля")
            page.select_option("#preset-select", "custom")
            page.wait_for_timeout(150)
            if not page.locator("#custom-block").is_visible():
                errors.append(f"{scheme}: поля своих правил не показались")
            page.click("#btn-example")
            page.wait_for_timeout(120)
            if "##" not in page.input_value("[data-k=custom_template]"):
                errors.append(f"{scheme}: пример шаблона не подставился")
            page.screenshot(path=str(out_dir / f"8-custom-{scheme}.png"))
            page.select_option("#preset-select", "meeting")
            page.wait_for_timeout(120)
            if "Различать собеседников по голосам" not in (page.text_content("#settings-body") or ""):
                errors.append(f"{scheme}: нет настройки разделения собеседников")

            # проверки содержимого
            page.click("#btn-close")
            # на экране теперь и карточка из архива — целимся в нужную
            page.click('.job[data-id="demo1234"] [data-tab="transcript"]')
            page.wait_for_timeout(150)
            text = page.inner_text("#pane-demo1234")
            for probe in ("00:00:12", "Спикер 1", "Давайте начнём с релиза"):
                if probe not in text:
                    errors.append(f"{scheme}: в транскрипте нет «{probe}»")
            # --- выделение и копирование ---
            picked = page.evaluate("""() => {
              const pane = document.querySelector('#pane-demo1234');
              const style = getComputedStyle(pane);
              const range = document.createRange();
              range.selectNodeContents(pane);
              const sel = window.getSelection();
              sel.removeAllRanges(); sel.addRange(range);
              return {select: style.webkitUserSelect || style.userSelect,
                      chrome: getComputedStyle(document.querySelector('.tabs')).userSelect,
                      picked: String(window.getSelection()).slice(0, 40)};
            }""")
            if picked["select"] == "none":
                errors.append(f"{scheme}: текст записи нельзя выделить")
            if picked["chrome"] != "none":
                errors.append(f"{scheme}: выделяются кнопки и вкладки")
            if len(picked["picked"]) < 10:
                errors.append(f"{scheme}: выделение пустое")
            if not page.locator('[data-copytab="demo1234"]').count():
                errors.append(f"{scheme}: нет кнопки копирования вкладки")
            page.click('[data-copytab="demo1234"]')
            page.wait_for_timeout(200)
            if "Скопировано" not in (page.text_content("#toast") or ""):
                errors.append(f"{scheme}: копирование вкладки не отработало")
            # что именно легло в буфер: текст раздела, а не html
            page.evaluate("state.tabs.set('demo1234','transcript'); render();")
            page.wait_for_timeout(150)
            page.click('[data-copytab="demo1234"]')
            page.wait_for_timeout(250)
            got = page.evaluate(
                "window.__copied || paneText(state.jobs.get('demo1234'),'transcript')")
            if "00:00:12" not in got or "Спикер 1" not in got or "<" in got:
                errors.append(f"{scheme}: в буфер ушёл не транскрипт: {got[:60]!r}")

            page.click('.job[data-id="demo1234"] [data-tab="tasks"]')
            page.wait_for_timeout(120)
            if page.locator("#pane-demo1234 table tbody tr").count() != 3:
                errors.append(f"{scheme}: таблица задач отрисована неверно")
            if page.locator(".chip").count() != 4:
                errors.append(f"{scheme}: индикаторы состояния не отрисовались")
            page.close()
        browser.close()

    for e in errors:
        print("  ✗", e)
    print(("  ✓ интерфейс отрисован без ошибок" if not errors else "  ошибки выше")
          + f"\n  скриншоты: {out_dir}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
