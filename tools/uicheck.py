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
    "people": ["Ирина", "Дмитрий"],
    "mode": "call",
    # Голоса, различённые по ходу разговора: у реплик уже есть подпись.
    "voices": [{"key": "V1", "name": "Собеседник 1", "lines": 1}],
    "lines": [
        {"start": 12.0, "who": "me", "label": "Я", "index": 0, "tagged": False,
         "voice": "", "text": "Давайте начнём с релиза."},
        {"start": 19.5, "who": "them", "label": "Собеседник 1", "index": 1,
         "tagged": False, "voice": "V1", "text": "Успеваем всё, кроме онбординга."},
        {"start": 31.0, "who": "me", "label": "Я", "index": 2, "tagged": False,
         "voice": "", "text": "Тогда режем до трёх экранов."},
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


# --- то же самое по-английски: для картинок в английском README и для
# проверки, что окно собирается на другом языке с другими данными.

EN_SECTIONS = {
    "summary": "- Agreed the scope of the first release and moved two tasks to the next sprint.\n"
               "- The contractor budget is approved at **£7,400**.\n"
               "- The 1C integration is still open — waiting on finance.",
    "brief": "### Context\nWeekly product team meeting on release 2.4.\n\n"
             "### Topics discussed\n- Release scope and what moves\n- Contractor budget\n"
             "- The 1C integration\n\n### Key points\n- Onboarding is cut to three screens.\n"
             "- We hold the dates by cutting scope, not quality.",
    "tasks": "| Task | Owner | Deadline |\n|---|---|---|\n"
             "| Prepare the onboarding mockups | Irina | 28 August |\n"
             "| Sign off the contractor estimate | Dmitry | 1 September |\n"
             "| Clarify the 1C requirements | Sergei | — |",
    "decisions": "- Onboarding is cut to three screens.\n- The contractor is approved, up to £7,400.",
    "risks": "- No answer from finance on 1C — blocks the estimate.\n"
             "- A developer's holiday from 5 September is not in the plan.",
}

EN_ESTIMATE_SECTIONS = {
    "works": "| Job | Quantity | Unit | Rate | Amount |\n|---|---|---|---|---|\n"
             "| Take down the partition | 12 | hour | 30 | 360 |\n"
             "| Chase the walls | 8 | hour | 25 | 200 |\n"
             "| **Total** |  |  |  | **560** |",
    "terms": "- Paid on acceptance.",
    "open": "- No price was named for the doors.",
    "summary": "- An estimate for rough works in a two-room flat.",
}

EN_TURNS = [
    (12.4, 41.8, "S1", "Let's start with the release. The plan says the 28th, and I want to "
                       "understand what we actually make."),
    (42.1, 58.0, "S2", "Everything except onboarding. Three screens out of five are done."),
    (58.4, 96.2, "S1", "Then we cut it to three screens and move the rest. Irina, can you "
                       "have the mockups by the 28th?"),
    (96.5, 104.0, "S3", "I can, but I need the final copy by Wednesday."),
    (104.2, 151.9, "S2", "The copy will be there. One more thing about the contractor — the "
                         "estimate came in at seven and a half thousand, we need to sign it off."),
]

EN_JOB = {
    **JOB, "title": "Product team 25.08.mp4",
    "meta": {"duration": 3187, "language": "en"},
    "summary_md": "## Summary\n" + EN_SECTIONS["summary"],
    "summary_sections": EN_SECTIONS,
    "speakers": {
        "S1": {"label": "Speaker 1", "seconds": 1284.0},
        "S2": {"label": "Speaker 2", "seconds": 902.5},
        "S3": {"label": "Speaker 3", "seconds": 431.2},
    },
    "turns": [{"start": s, "end": e, "speaker": spk, "text": x} for s, e, spk, x in EN_TURNS],
}

EN_RUNNING = {
    **EN_JOB, "id": "demo5678", "title": "Customer interview.mov", "status": "running",
    "stage": "asr", "message": "Recognising, part 2 of 6", "progress": 0.34,
    "files": {}, "summary_sections": {}, "turns": [], "speakers": {},
    "warnings": ["Speaker separation failed: the models are not downloaded yet"],
}

EN_ESTIMATE_JOB = {
    **ESTIMATE_JOB, "title": "Renovation estimate.m4a",
    "meta": {"duration": 214, "language": "en"},
    "summary_md": "## Work\n" + EN_ESTIMATE_SECTIONS["works"],
    "summary_sections": EN_ESTIMATE_SECTIONS,
    "summary_tabs": [["works", "Work"], ["terms", "Terms"],
                     ["open", "To clarify"], ["summary", "In short"]],
    "files": {"summary": "/Users/sam/output/Renovation estimate.summary.md",
              "tables": "/Users/sam/output/Renovation estimate.tables.csv"},
    "turns": [{"start": 3.0, "end": 9.0, "speaker": "S1",
               "text": "So, taking down the partition — twelve hours at thirty."}],
}

EN_REC = {
    **REC, "title": "Call 25.08 21-40",
    "message": "6 min recorded, 14 lines",
    "notes": [{"at": 300, "text": "- Went over the release dates\n- Irina takes the mockups"}],
    "people": ["Irina", "Dmitry"],
    "lines": [
        {"start": 12.0, "who": "me", "label": "Me", "index": 0, "tagged": False,
         "text": "Let's start with the release."},
        {"start": 19.5, "who": "them", "label": "Them", "index": 1, "tagged": False,
         "text": "Everything except onboarding."},
        {"start": 31.0, "who": "me", "label": "Me", "index": 2, "tagged": False,
         "text": "Then we cut it to three screens."},
    ],
}

EN_LIB = [
    {**LIB[0], "title": "Call 26.08 07-30",
     "preview": "Agreed the scope of the first release and moved two tasks."},
    {**LIB[1], "title": "Renovation estimate",
     "preview": "Partition demolition, wall chasing, waste removal."},
    {**LIB[2], "title": "Product team 25.08.mp4",
     "preview": "Weekly product team meeting on release 2.4."},
]

PEOPLE = {
    "items": [
        {"name": "Ирина Волкова", "org": "Подрядчик", "role": "прораб", "voice": True},
        {"name": "Сергей Ким", "org": "Подрядчик", "role": "", "voice": False},
        {"name": "Дмитрий", "org": "Наш продукт", "role": "дизайн", "voice": False},
    ],
    "orgs": [
        {"org": "Наш продукт", "people": ["Дмитрий"]},
        {"org": "Подрядчик", "people": ["Ирина Волкова", "Сергей Ким"]},
    ],
}

EN_PEOPLE = {
    "items": [
        {"name": "Irina Volkova", "org": "Contractor", "role": "site manager", "voice": True},
        {"name": "Sergey Kim", "org": "Contractor", "role": "", "voice": False},
        {"name": "Dmitry", "org": "Our product", "role": "design", "voice": False},
    ],
    "orgs": [
        {"org": "Contractor", "people": ["Irina Volkova", "Sergey Kim"]},
        {"org": "Our product", "people": ["Dmitry"]},
    ],
}

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
  rec_name_voice: async (key, name) => { window.__voiceNamed = {key: key, name: name};
                                         return null; },
  rec_request: async () => ({screen: true, microphone: true}),
  rec_start: async (title, preset, mode) => {
    window.__recMode = mode;
    return {ok: true, session: Object.assign({}, %(rec)s, {mode: mode})};
  },
  rec_stop: async () => (%(rec)s),
  rec_cancel: async () => true,
  rec_people: async names => {
    window.__people = names;
    return Object.assign({}, %(rec)s, {people: names});
  },
  rec_tag: async (index, name) => {
    window.__tag = {index: index, name: name};
    const session = JSON.parse(JSON.stringify(%(rec)s));
    if (name) {
      session.lines[index].label = name;
      session.lines[index].tagged = true;
    }
    return session;
  },
  open_privacy: async () => true,
  presets: async () => (%(presets)s),
  library: async q => ({items: (%(lib)s).filter(
      i => !q || (i.title + " " + i.preview).toLowerCase().includes(q.toLowerCase())),
    dir: "/Users/sergey/output"}),
  library_open: async id => Object.assign({}, %(estimate)s, {id: id, archived: true,
    title: (%(lib)s).find(i => i.id === id).title, message: "Из архива"}),
  library_delete: async () => ({ok: true, removed: 5}),
  trash: async () => ({days: 30, items: [
    {id: "1756000000-abc", title: "Созвон 24.08 10-15", when: "2026-08-28 12:40",
     days_left: 30, files: 6}]}),
  trash_restore: async id => { window.__restored = id;
                               return {ok: true, restored: 6, title: "Созвон 24.08 10-15"}; },
  trash_purge: async id => { window.__purged = id; return {ok: true, removed: 1}; },
  attachments: async () => ({items: [
    {name: "Смета подрядчика.pdf", path: "/x/Смета подрядчика.pdf", size: 184320,
     kind: "doc", readable: true},
    {name: "Схема интеграции.png", path: "/x/Схема интеграции.png", size: 96000,
     kind: "image", readable: false}]}),
  attach_preview: async (id, name, size) => { window.__preview = {name: name, size: size};
    return "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '">' +
      '<rect width="100%%" height="100%%" fill="#4d7cff"/></svg>'))); },
  attach_add: async id => { window.__attached = id;
    return {ok: true, added: ["Письмо от заказчика.docx"], items: [
      {name: "Смета подрядчика.pdf", path: "/x/1.pdf", size: 184320, kind: "doc", readable: true},
      {name: "Схема интеграции.png", path: "/x/2.png", size: 96000, kind: "image", readable: false},
      {name: "Письмо от заказчика.docx", path: "/x/3.docx", size: 24576, kind: "doc",
       readable: true}]}; },
  attach_remove: async (id, name) => { window.__detached = name;
    return {ok: true, items: [
      {name: "Смета подрядчика.pdf", path: "/x/1.pdf", size: 184320,
       kind: "doc", readable: true}]}; },
  resummarize: async id => { window.__resum = id;
    return {ok: true, job: Object.assign({}, %(estimate)s, {id: "resum1",
      status: "running", stage: "summary", progress: 0.3,
      message: "Сборка итогового документа · Ollama"})}; },
  library_rename: async () => (%(estimate)s),
  voices_list: async () => ({items: [{name: "Леонид", prints: 6},
                                     {name: "Марина", prints: 3}]}),
  voices_learn: async id => { window.__learned = id;
                              return {ok: true, learned: {"Леонид": 6}}; },
  voices_forget: async name => { window.__forgot = name; return {ok: true}; },
  people_list: async () => (%(people)s),
  people_add: async (name, org) => { window.__personAdded = {name: name, org: org};
                                     return Object.assign({ok: true}, %(people)s); },
  people_remove: async name => { window.__personGone = name;
                                 return Object.assign({ok: true}, %(people)s); },
  copy: async text => { window.__copied = text; return true; },
  edit_summary: async (id, key, markdown) => {
    window.__edited = {id: id, key: key, markdown: markdown};
    const sections = Object.assign({}, %(sections)s);
    sections[key] = markdown;
    return {ok: true, sections: sections, markdown: markdown, tables: false};
  },
}};
"""


DOCS_DIR = ROOT / "docs" / "screenshots"


def docs_shot(page, scheme: str, want: str, name: str) -> None:
    """Кладёт картинку в docs/screenshots — но только с флагом --docs.

    Снимки для README снимаются здесь же, в проверке интерфейса: иначе они
    отстают от кода на несколько версий, как и вышло с 1.0.0.
    """
    if "--docs" not in sys.argv or scheme != want:
        return
    # Прибираемся перед съёмкой: уводим курсор от кнопок и гасим всплывашку,
    # оставшуюся от предыдущего клика, — на картинке для README она закрывает
    # половину таблицы.
    page.mouse.move(2, 2)
    page.evaluate("document.querySelector('#toast')?.classList.remove('show')")
    page.wait_for_timeout(400)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(DOCS_DIR / name))


def _english_pass(browser, settings: dict, env: dict, presets_mod, out_dir: Path) -> list[str]:
    """Собирает окно по-английски: проверяет перевод и снимает картинки."""
    import re

    from app import i18n

    errors: list[str] = []
    i18n.use("en")
    english = dict(settings, ui_language="en", doc_language="en")
    catalogue = {"items": presets_mod.catalogue("en"), "current": "meeting",
                 "example": presets_mod.custom_example("en")}
    bridge = BRIDGE % {"settings": json.dumps(english), "env": json.dumps(env),
                       "rec": json.dumps(EN_REC), "presets": json.dumps(catalogue),
                       "lib": json.dumps(EN_LIB), "estimate": json.dumps(EN_ESTIMATE_JOB),
                       "sections": json.dumps(EN_SECTIONS),
                       "people": json.dumps(EN_PEOPLE)}

    for scheme in ("light", "dark"):
        page = browser.new_page(viewport={"width": 1180, "height": 820},
                                color_scheme=scheme)
        page.on("pageerror", lambda e, s=scheme: errors.append(f"en/{s}: {e}"))
        page.on("console", lambda m, s=scheme: errors.append(f"en/{s}: {m.text}")
                if m.type == "error" else None)
        page.add_init_script(bridge)
        page.goto(UI.as_uri())
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        page.wait_for_timeout(300)

        for probe in ("Archive", "Settings", "Choose a recording"):
            if probe not in (page.text_content("#app") or ""):
                errors.append(f"en/{scheme}: в окне нет «{probe}»")

        page.evaluate("(j)=>{state.jobs.set(j.id,j); render();}", EN_RUNNING)
        page.evaluate("(j)=>{state.jobs.set(j.id,j); openJob(j.id);}", EN_JOB)
        page.wait_for_timeout(150)
        page.click('.job[data-id="demo1234"] [data-tab="tasks"]')
        page.wait_for_timeout(120)
        page.click('[data-rec="start"]')
        page.wait_for_timeout(300)
        page.screenshot(path=str(out_dir / f"12-en-{scheme}.png"))
        docs_shot(page, scheme, "light", "main.png")

        # Кириллица в собственных надписях окна означает забытую строку.
        # Данные (названия записей, реплики) остаются русскими — их исключаем.
        chrome = page.evaluate("""() => {
          const parts = [];
          document.querySelectorAll('header, .picker, .drop, .tabs, .side-head,'
            + ' .side-foot, .rec-head, .people, .actions').forEach(el => {
            parts.push(el.innerText || '');
          });
          return parts.join(' | ');
        }""")
        stray = [w for w in re.findall(r"[А-Яа-яЁё][А-Яа-яЁё-]+", chrome)
                 if w not in ("Созвон", "Встреча", "Собеседник", "Я", "Интервью",
                              "клиентом", "команды", "Записано", "мин", "реплик",
                              # имена участников — это данные, а не интерфейс
                              "Ирина", "Дмитрий", "Сергей")]
        if stray:
            errors.append(f"en/{scheme}: непереведённое в окне — {sorted(set(stray))[:6]}")

        page.click('[data-rec="cancel"]')
        page.wait_for_timeout(150)
        page.evaluate("(j)=>{state.jobs.set(j.id,j); openJob(j.id);}", EN_ESTIMATE_JOB)
        page.wait_for_timeout(150)
        page.click('.job[data-id="demo9999"] [data-tab="works"]')
        page.wait_for_timeout(150)
        docs_shot(page, scheme, "light", "estimate.png")

        page.evaluate("state.jobs.delete('demo9999'); render();")
        page.click('[data-lib="lib1"]')
        page.wait_for_timeout(300)
        docs_shot(page, scheme, "dark", "archive-dark.png")
        page.close()

    i18n.use("ru")
    return errors


def main() -> int:
    from playwright.sync_api import sync_playwright

    plain = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = Path(plain[0] if plain else "/tmp/uicheck")
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "ui_language": "ru", "doc_language": "auto",
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
    from app import i18n
    from app import presets as presets_mod
    # Настоящий Api.__init__ делает то же самое при старте: язык окна берётся
    # из settings["ui_language"], а auto/отсутствие поля разворачивается в
    # язык системы. Тест эмулирует настройки без ui_language — окно в нём
    # по умолчанию русское (см. index.html), так что и профили здесь должны
    # прийти русскими, иначе подсказки в интерфейсе и в этом словаре разойдутся.
    i18n.use("ru")
    catalogue = {"items": presets_mod.catalogue(), "current": "meeting",
                 "example": presets_mod.CUSTOM_EXAMPLE}
    bridge = BRIDGE % {"settings": json.dumps(settings), "env": json.dumps(env),
                       "rec": json.dumps(REC), "presets": json.dumps(catalogue),
                       "lib": json.dumps(LIB), "estimate": json.dumps(ESTIMATE_JOB),
                       "sections": json.dumps(SUMMARY_SECTIONS),
                       "people": json.dumps(PEOPLE)}

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

            page.evaluate("(j)=>{state.jobs.set(j.id,j); render();}", RUNNING)
            page.evaluate("(j)=>{state.jobs.set(j.id,j); openJob(j.id);}", JOB)
            page.wait_for_timeout(200)
            # В рабочей области — только открытая запись, остальные строкой сверху.
            if page.locator(".job").count() != 1:
                errors.append(f"{scheme}: в рабочей области больше одной записи: "
                              f"{page.locator('.job').count()}")
            if not page.locator('[data-openjob="demo5678"]').count():
                errors.append(f"{scheme}: идущее задание не показано строкой")
            page.click('[data-openjob="demo5678"]')
            page.wait_for_timeout(250)
            if not page.locator('.job[data-id="demo5678"]').count():
                errors.append(f"{scheme}: по клику строка не открывает задание")
            page.evaluate("openJob('demo1234')")
            page.wait_for_timeout(200)
            page.wait_for_timeout(150)
            page.screenshot(path=str(out_dir / f"2-result-{scheme}.png"), full_page=True)

            page.click('[data-tab="tasks"]')
            page.wait_for_timeout(150)
            page.screenshot(path=str(out_dir / f"3-tasks-{scheme}.png"), full_page=True)

            # --- панель стенографиста ---
            if not page.locator('[data-rec="start"]').count():
                errors.append(f"{scheme}: нет кнопки начала записи")
            if not page.locator('[data-rec="start-room"]').count():
                errors.append(f"{scheme}: нет кнопки записи встречи")
            page.click('[data-rec="start-room"]')
            page.wait_for_timeout(250)
            if page.evaluate("window.__recMode || ''") != "room":
                errors.append(f"{scheme}: встреча запустилась не в своём режиме")
            page.click('[data-rec="cancel"]')
            page.wait_for_timeout(150)
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
            docs_shot(page, scheme, "light", "main.ru.png")

            # --- голоса, узнанные по ходу ---
            if not page.locator('[data-voice="V1"]').count():
                errors.append(f"{scheme}: голос из записи не показан в строке голосов")
            if "Собеседник 1" not in (page.text_content("#rec") or ""):
                errors.append(f"{scheme}: реплика собеседника без номера голоса")
            page.click('[data-voice="V1"]')
            page.wait_for_timeout(150)
            if not page.locator("#voice-rename").count():
                errors.append(f"{scheme}: голос нельзя переименовать по клику")
            else:
                page.fill("#voice-rename", "Ирина")
                page.press("#voice-rename", "Enter")
                page.wait_for_timeout(250)
                named = page.evaluate("window.__voiceNamed || null")
                if not named or named["key"] != "V1" or named["name"] != "Ирина":
                    errors.append(f"{scheme}: имя голоса не ушло в Python: {named}")

            # --- разметка голосов по ходу ---
            if page.locator(".person").count() < 2:
                errors.append(f"{scheme}: список участников не отрисовался")
            page.click('.rec-line[data-pick="1"]')
            page.wait_for_timeout(150)
            if not page.locator(".pickbar .person").count():
                errors.append(f"{scheme}: по клику на реплику не предложили имена")
            page.screenshot(path=str(out_dir / f"11-people-{scheme}.png"))
            page.locator('.pickbar [data-name="Дмитрий"]').click()
            page.wait_for_timeout(250)
            tag = page.evaluate("window.__tag || null")
            if not tag or tag["index"] != 1 or tag["name"] != "Дмитрий":
                errors.append(f"{scheme}: реплика не привязалась к человеку: {tag}")
            if not page.locator(".rec-line.tagged").count():
                errors.append(f"{scheme}: отмеченная реплика не выделена")
            # цифрой отмечается последняя реплика
            page.keyboard.press("1")
            page.wait_for_timeout(250)
            tag = page.evaluate("window.__tag || null")
            if not tag or tag["index"] != 2 or tag["name"] != "Ирина":
                errors.append(f"{scheme}: цифра не отметила последнюю реплику: {tag}")
            page.fill("#person-add", "Сергей")
            page.press("#person-add", "Enter")
            page.wait_for_timeout(250)
            people = page.evaluate("window.__people || []")
            if "Сергей" not in people:
                errors.append(f"{scheme}: участник не добавился: {people}")
            page.screenshot(path=str(out_dir / f"6-record-{scheme}.png"))

            # --- длинный разговор: прокрутка не убегает вниз ---
            # Раньше любой клик по реплике перерисовывал окно и швырял человека
            # в конец записи — приходилось искать реплику заново.
            page.evaluate("""() => {
              const lines = [];
              for (let i = 0; i < 80; i++) {
                lines.push({start: i * 5, who: 'them', label: 'Собеседник',
                            text: 'реплика номер ' + i, tagged: false,
                            voice: 'V1', index: i});
              }
              setRec(Object.assign({}, state.rec, {lines: lines, line_count: 80}));
              renderRec();
            }""")
            page.wait_for_timeout(200)
            page.evaluate("document.querySelector('#rec-live').scrollTop = 300")
            page.wait_for_timeout(120)
            page.click('.rec-line[data-pick="12"]')
            page.wait_for_timeout(250)
            top = page.evaluate("document.querySelector('#rec-live').scrollTop")
            if abs(top - 300) > 40:
                errors.append(f"{scheme}: после клика по реплике прокрутка уехала: {top}")
            if not page.locator(".pickbar").count():
                errors.append(f"{scheme}: выбор имени не открылся на середине записи")
            if page.locator("#rec-down").is_hidden():
                errors.append(f"{scheme}: нет кнопки возврата к последним репликам")
            page.click("#rec-down")
            page.wait_for_timeout(200)
            if not page.evaluate(
                    "atBottom(document.querySelector('#rec-live'))"):
                errors.append(f"{scheme}: кнопка не вернула к последним репликам")

            # --- часы записи идут вровень со временем разговора ---
            shown = page.evaluate(
                "setRec({state:'recording', duration:600, lines:[], people:[],"
                " voices:[], notes:[], title:'т', message:''}) && recTime()")
            if shown != "00:10:00":
                errors.append(f"{scheme}: часы записи врут: {shown} вместо 00:10:00")

            page.click('[data-rec="cancel"]')
            page.wait_for_timeout(150)

            # --- профиль: смета со своими вкладками ---
            page.evaluate("(j)=>{state.jobs.set(j.id,j); openJob(j.id);}", ESTIMATE_JOB)
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
            docs_shot(page, scheme, "light", "estimate.ru.png")
            page.evaluate("state.jobs.delete('demo9999'); render();")
            page.wait_for_timeout(100)

            # --- переключатель профиля ---
            if not page.locator("#preset-pick").count():
                errors.append(f"{scheme}: нет выбора профиля")
            page.select_option("#preset-pick", "estimate")
            page.wait_for_timeout(150)
            if "смет" not in (page.text_content("#picker") or "").lower():
                errors.append(f"{scheme}: подсказка профиля не обновилась")

            # --- правка саммари: вычеркнуть лишнее ---
            page.evaluate("openJob('demo1234')")
            page.wait_for_timeout(200)
            page.click('.job[data-id="demo1234"] [data-tab="summary"]')
            page.wait_for_timeout(150)
            if not page.locator('[data-edittab="demo1234"]').count():
                errors.append(f"{scheme}: нет кнопки правки саммари")
            page.click('[data-edittab="demo1234"]')
            page.wait_for_timeout(200)
            items = page.locator("#pane-demo1234 .edit-item")
            if items.count() != 3:
                errors.append(f"{scheme}: пункты саммари не разобрались ({items.count()})")
            page.locator("#pane-demo1234 .edit-item .kill").nth(1).click()
            page.wait_for_timeout(120)
            if not page.locator("#pane-demo1234 .edit-item.gone").count():
                errors.append(f"{scheme}: пункт не вычеркнулся")
            page.screenshot(path=str(out_dir / f"10-edit-{scheme}.png"))
            page.click('[data-editsave="demo1234"]')
            page.wait_for_timeout(300)
            edited = page.evaluate("window.__edited || null")
            if not edited:
                errors.append(f"{scheme}: правка не отправилась в Python")
            elif "740" in edited["markdown"] or edited["markdown"].count("\n") != 1:
                errors.append(f"{scheme}: сохранился не тот текст: {edited['markdown'][:70]!r}")
            if page.locator("#pane-demo1234 .edit-item").count():
                errors.append(f"{scheme}: после сохранения режим правки не закрылся")

            # --- правка таблицы: строка и пересчёт ---
            page.click('.job[data-id="demo1234"] [data-tab="tasks"]')
            page.wait_for_timeout(150)
            page.click('[data-edittab="demo1234"]')
            page.wait_for_timeout(200)
            rows = page.locator("#pane-demo1234 .edit-table .row[data-row]")
            if rows.count() != 3:
                errors.append(f"{scheme}: строки таблицы не разобрались ({rows.count()})")
            page.locator("#pane-demo1234 .edit-table .row[data-row] .kill").nth(0).click()
            page.wait_for_timeout(120)
            page.click('[data-editsave="demo1234"]')
            page.wait_for_timeout(300)
            edited = page.evaluate("window.__edited || null")
            if not edited or "Ирина" in edited["markdown"] or "Дмитрий" not in edited["markdown"]:
                errors.append(f"{scheme}: строка таблицы удалилась неверно")
            elif "---" not in edited["markdown"].split("\n")[1]:
                errors.append(f"{scheme}: разделитель таблицы потерялся: "
                              f"{edited['markdown'][:120]!r}")

            # Возвращаем исходные разделы: их проверяют следующие шаги.
            page.evaluate("(s)=>{state.jobs.get('demo1234').summary_sections=s; render();}",
                          SUMMARY_SECTIONS)
            page.wait_for_timeout(150)

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
            # Клик по архиву показывает только эту запись — ленты больше нет.
            if page.locator(".job").count() != 1:
                errors.append(f"{scheme}: в рабочей области не одна запись: "
                              f"{page.locator('.job').count()}")
            # зона перетаскивания скрыта карточками — кнопка выбора файла
            # обязана оставаться на виду
            if not page.locator("#btn-pick-top").is_visible():
                errors.append(f"{scheme}: пропала кнопка выбора записи")
            page.click("#btn-pick-top")
            page.wait_for_timeout(120)
            page.screenshot(path=str(out_dir / f"9-library-{scheme}.png"))
            docs_shot(page, scheme, "dark", "archive-dark.ru.png")
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

            # --- документы к записи ---
            page.click("#btn-close")          # выходим из настроек
            page.wait_for_timeout(250)
            page.evaluate("openJob('demo1234')")
            page.evaluate("loadDocs('demo1234')")
            page.wait_for_timeout(300)
            # Документы свёрнуты по умолчанию и раскрываются кликом.
            if page.locator(".docs .inner").is_visible():
                errors.append(f"{scheme}: документы не свёрнуты по умолчанию")
            page.click(".docs summary")
            page.wait_for_timeout(200)
            if not page.locator(".docs .inner").is_visible():
                errors.append(f"{scheme}: документы не раскрываются")
            docs = page.text_content(".docs") or ""
            for probe in ("Документы к записи", "Смета подрядчика.pdf", "Схема интеграции.png"):
                if probe not in docs:
                    errors.append(f"{scheme}: в блоке документов нет «{probe}»")
            if not page.locator('[data-resum="demo1234"]').count():
                errors.append(f"{scheme}: нет кнопки пересборки саммари")
            if not page.locator('[data-docadd="demo1234"].primary svg').count():
                errors.append(f"{scheme}: кнопка «приложить» не акцентная и без плюса")
            page.screenshot(path=str(out_dir / f"14-docs-{scheme}.png"))
            page.click('[data-docadd="demo1234"]')
            page.wait_for_timeout(300)
            if page.evaluate("window.__attached") != "demo1234":
                errors.append(f"{scheme}: документ не прикладывается")
            if "Письмо от заказчика.docx" not in (page.text_content(".docs") or ""):
                errors.append(f"{scheme}: приложенный документ не появился в списке")
            # Картинки — отдельной полкой с квадратными превью.
            if not page.locator(".docs .shots .shot .pic img").count():
                errors.append(f"{scheme}: у картинки нет превью")
            if page.locator('.docs .list .doc:has-text("Схема интеграции.png")').count():
                errors.append(f"{scheme}: картинка попала в список документов")
            page.wait_for_timeout(300)
            if not page.evaluate("document.querySelector('.docs .shot img').src.length > 100"):
                errors.append(f"{scheme}: превью не подгрузилось")
            # Крестики появляются только в режиме правки.
            if page.locator(".docs .x").count():
                errors.append(f"{scheme}: удаление доступно без режима правки")
            page.click('[data-docedit="demo1234"]')
            page.wait_for_timeout(250)
            if not page.locator(".docs .x").count():
                errors.append(f"{scheme}: в режиме правки нет крестиков")
            page.screenshot(path=str(out_dir / f"15-docs-edit-{scheme}.png"))
            page.click('[data-docdel="Смета подрядчика.pdf"]')
            page.wait_for_timeout(250)
            if page.evaluate("window.__detached") != "Смета подрядчика.pdf":
                errors.append(f"{scheme}: документ не убирается")
            page.click('[data-docedit="demo1234"]')      # выходим из правки
            page.wait_for_timeout(200)
            # Картинка открывается во весь экран и закрывается по Escape.
            page.evaluate("loadDocs('demo1234')")
            page.wait_for_timeout(300)
            page.click(".docs .shot .pic")
            page.wait_for_timeout(400)
            if not page.locator(".lightbox img").count():
                errors.append(f"{scheme}: картинка не открывается крупно")
            if (page.evaluate("window.__preview") or {}).get("size") != 1600:
                errors.append(f"{scheme}: крупное превью запрошено не тем размером")
            page.screenshot(path=str(out_dir / f"16-shot-{scheme}.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            if page.locator(".lightbox").count():
                errors.append(f"{scheme}: картинка не закрывается по Escape")
            page.click('[data-docopen="/x/Смета подрядчика.pdf"]')
            page.wait_for_timeout(200)
            page.click('[data-resum="demo1234"]')
            page.wait_for_timeout(300)
            if page.evaluate("window.__resum") != "demo1234":
                errors.append(f"{scheme}: пересборка саммари не запускается")

            # --- корзина: удаление, возврат, срок ---
            page.click("#btn-trash")
            page.wait_for_timeout(300)
            trash_text = page.text_content("#lib-list") or ""
            for probe in ("Созвон 24.08 10-15", "осталось дней: 30", "Вернуть"):
                if probe not in trash_text:
                    errors.append(f"{scheme}: в корзине нет «{probe}»")
            if "Корзина" not in (page.text_content("#lib-count") or "") + trash_text:
                errors.append(f"{scheme}: корзина не подписана")
            page.screenshot(path=str(out_dir / f"13-trash-{scheme}.png"))
            page.click('[data-restore="1756000000-abc"]')
            page.wait_for_timeout(300)
            if page.evaluate("window.__restored") != "1756000000-abc":
                errors.append(f"{scheme}: запись не возвращается из корзины")
            page.click("#btn-trash")          # обратно к записям
            page.wait_for_timeout(250)
            if not page.locator('[data-lib="lib1"]').count():
                errors.append(f"{scheme}: из корзины не вернуться к записям")
            if not page.locator('[data-libdel="lib1"] svg').count():
                errors.append(f"{scheme}: у удаления нет иконки корзины")
            page.click("#btn-settings")
            page.wait_for_timeout(300)

            # --- справочник людей и команд ---
            if "Люди и команды" not in (page.text_content("#settings-body") or ""):
                errors.append(f"{scheme}: нет раздела справочника")
            directory = page.text_content("#people-list") or ""
            for probe in ("Подрядчик", "Ирина Волкова", "голос запомнен", "Наш продукт"):
                if probe not in directory:
                    errors.append(f"{scheme}: в справочнике нет «{probe}»")
            page.fill("#person-name", "Ольга Фокина")
            page.fill("#person-org", "Заказчик")
            page.click("#btn-person-add")
            page.wait_for_timeout(250)
            added = page.evaluate("window.__personAdded")
            if not added or added["name"] != "Ольга Фокина" or added["org"] != "Заказчик":
                errors.append(f"{scheme}: человек не добавляется: {added}")
            page.click('[data-personout="Сергей Ким"]')
            page.wait_for_timeout(250)
            if page.evaluate("window.__personGone") != "Сергей Ким":
                errors.append(f"{scheme}: человек не убирается из справочника")

            # --- знакомые голоса ---
            if "Знакомые голоса" not in (page.text_content("#settings-body") or ""):
                errors.append(f"{scheme}: нет раздела знакомых голосов")
            if "Леонид" not in (page.text_content("#known-voices") or ""):
                errors.append(f"{scheme}: список знакомых голосов пуст")
            page.click('#known-voices [data-forget="Леонид"]')
            page.wait_for_timeout(250)
            if page.evaluate("window.__forgot") != "Леонид":
                errors.append(f"{scheme}: голос не забывается")

            # проверки содержимого
            page.click("#btn-close")
            # на экране теперь и карточка из архива — целимся в нужную
            page.evaluate("openJob('demo1234')")
            page.wait_for_timeout(200)
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
                errors.append(f"{scheme}: таблица задач отрисована неверно: "
                              f"{(page.text_content('#pane-demo1234') or '')[:120]!r}")
            if page.locator(".chip").count() != 4:
                errors.append(f"{scheme}: индикаторы состояния не отрисовались")

            # --- запоминание голосов по команде ---
            if not page.locator('[data-learn="demo1234"]').count():
                errors.append(f"{scheme}: нет кнопки «Запомнить голоса»")
            page.click('[data-learn="demo1234"]')
            page.wait_for_timeout(300)
            if page.evaluate("window.__learned") != "demo1234":
                errors.append(f"{scheme}: команда запомнить голоса не дошла")
            if "Запомнил" not in (page.text_content("#toast") or ""):
                errors.append(f"{scheme}: нет ответа на запоминание голосов")
            page.close()

        # --- английское окно ---
        # Отдельным проходом, а не вторым витком цикла: проверки выше ищут
        # русские строки, и переписывать их «под язык» — значит проверять
        # словарь словарём. Здесь важно другое: что окно вообще собирается,
        # нигде не осталось русского и картинки для английского README
        # снимаются с того же состояния, что и русские.
        errors += _english_pass(browser, settings, env, presets_mod, out_dir)
        browser.close()

    for e in errors:
        print("  ✗", e)
    print(("  ✓ интерфейс отрисован без ошибок" if not errors else "  ошибки выше")
          + f"\n  скриншоты: {out_dir}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
