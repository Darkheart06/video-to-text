#!/usr/bin/env python3
"""Замер качества разбора на своих записях.

Чужие бенчмарки моделей отвечают на вопрос «какая модель лучше в среднем по
интернету». Нужный вопрос другой: какая модель лучше на *этих* созвонах — с
этими микрофонами, этой комнатой, этими людьми, перебивающими друг друга.
Разброс между записями больше разброса между моделями, поэтому единственный
честный ответ получается замером на своём материале.

Как пользоваться:

1. Завести эталон из уже разобранной записи:

       python tools/bench.py --new "~/Documents/Расшифровка записей/Созвон 2026-08-27 11-10.wav"

   Рядом ляжет файл `<запись>.ref.txt` — расшифровка с именами. **Его нужно
   прочитать и поправить руками**: имена спикеров и слова. Это и есть эталон;
   пока он не выверен, замер меряет совпадение модели с самой собой.
   Достаточно поправить первые 10–15 минут и обрезать остаток: короткий
   выверенный кусок честнее часа непроверенного.

2. Прогнать варианты:

       python tools/bench.py                  # все эталоны, все варианты
       python tools/bench.py --only turbo,large-v3
       python tools/bench.py --list

Что печатается:

* **WER** — ошибки в словах (замены, пропуски, придуманное) к словам эталона.
* **WDER** — доля верно распознанных слов, подписанных не тем человеком.
  Именно это видно в расшифровке: текст правильный, а реплика приписана
  соседу.
* время разбора и во сколько раз оно быстрее самой записи.

Саммари не считается: языковая модель к качеству распознавания отношения не
имеет, а времени берёт больше всего.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import asr, cleanup, diarize, media, merge, metrics  # noqa: E402
from app.settings import Settings  # noqa: E402

# Варианты разбора: имя → что поменять в настройках. Меняется только то, что
# названо; остальное берётся из настроек приложения, чтобы замер шёл в тех же
# условиях, в которых работает программа.
VARIANTS: dict[str, dict] = {
    "turbo": {"whisper_model": "large-v3-turbo"},
    "large-v3": {"whisper_model": "large-v3"},
    # Разделение по голосам без сведения кластеров: видно, сколько даёт сама
    # сверка (`diarize.refine`), а сколько — модель отпечатков.
    "turbo-без-сведения": {"whisper_model": "large-v3-turbo",
                           "speaker_merge_auto": False},
    # Модель голосовых отпечатков: ею и разделяются голоса, и узнаются
    # знакомые. resnet293 точнее на проверочных наборах (EER 0.53% против
    # 0.71% у CAM++), но она вчетверо больше и считается заметно дольше.
    "turbo+resnet293": {"whisper_model": "large-v3-turbo",
                        "voice_model": "resnet293"},
    "large-v3+resnet293": {"whisper_model": "large-v3",
                           "voice_model": "resnet293"},
    "turbo+eres2netv2": {"whisper_model": "large-v3-turbo",
                         "voice_model": "eres2netv2"},
}

LINE = re.compile(r"^\s*(?:\[?(?P<at>\d{1,2}:\d{2}(?::\d{2})?)\]?\s+)?"
                  r"(?P<who>[^:|]{1,60})\s*[:|]\s*(?P<text>.+)$")


def refs_in(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.ref.txt"))


def read_ref(path: Path) -> list[tuple[str, str]]:
    """Эталон → реплики «кто, что». Строки не по формату пропускаем молча:
    файл правится руками, и пустая строка или заметка на полях — это нормально."""
    turns: list[tuple[str, str]] = []
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = LINE.match(line)
        if not found:
            continue
        turns.append((found.group("who").strip(), found.group("text").strip()))
    return turns


def make_ref(wav: Path) -> Path:
    """Заготовка эталона из уже разобранной записи."""
    data_path = wav.with_suffix(".result.json")
    if not data_path.exists():
        raise SystemExit(f"нет разбора рядом с записью: {data_path.name}")
    data = json.loads(data_path.read_text("utf-8"))
    speakers = {k: (v.get("label") or k) for k, v in (data.get("speakers") or {}).items()}
    lines = [
        "# Эталон для замера. Поправьте руками: имена спикеров и слова.",
        "# Формат строки:  [00:12] Имя: текст реплики",
        "# Строки, начинающиеся с #, не читаются — ими можно обрезать хвост.",
        f"# Запись: {wav.name}",
        "",
    ]
    for turn in data.get("turns") or []:
        who = speakers.get(turn.get("speaker") or "", turn.get("speaker") or "?")
        at = int(float(turn.get("start") or 0))
        lines.append(f"[{at // 60:02d}:{at % 60:02d}] {who}: {turn.get('text', '').strip()}")
    out = wav.with_suffix(".ref.txt")
    out.write_text("\n".join(lines) + "\n", "utf-8")
    return out


def run_variant(wav: Path, settings: Settings, changes: dict) -> tuple[list, float]:
    """Один прогон: распознавание и разделение по голосам, без саммари."""
    probe = Settings(dict(settings))
    probe.update(changes)
    started = time.time()
    work = ROOT / ".work" / "bench"
    work.mkdir(parents=True, exist_ok=True)
    audio = work / (wav.stem + ".wav")
    if not audio.exists():
        media.extract_wav(str(wav), audio)

    transcript = asr.transcribe(audio, probe)
    spans = []
    if probe["diarization_enabled"]:
        spans = diarize.diarize(audio, probe)
        merge.assign_speakers(transcript, spans)
    turns = merge.build_turns(transcript)
    cleanup.clean_turns(turns, bool(probe["transcript_cleanup"]))
    rows = [(t.speaker_key, t.text) for t in turns]
    return rows, time.time() - started


def check_models() -> int:
    """Какие модели голосовых отпечатков доступны для скачивания.

    Имена файлов в выпуске sherpa-onnx меняются от версии к версии, а узнать
    это можно только запросом. Проверяем каждую и печатаем размер: пусть
    список в коде отвечает за себя сам, а не выглядит правдоподобно.
    """
    import urllib.error
    import urllib.request

    print("Модели голосовых отпечатков:")
    for key in diarize.EMB_MODELS:
        url = diarize.emb_url(key)
        here = diarize.emb_path(key)
        if here.exists():
            print(f"  {key:12} уже скачана, {here.stat().st_size / 1e6:.0f} МБ")
            continue
        request = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                size = int(resp.headers.get("Content-Length") or 0)
            print(f"  {key:12} есть, {size / 1e6:.0f} МБ")
        except urllib.error.HTTPError as exc:
            print(f"  {key:12} НЕТ ({exc.code}) — {url}")
        except Exception as exc:
            print(f"  {key:12} не проверить: {exc}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Замер качества разбора на своих записях")
    parser.add_argument("--new", metavar="ЗАПИСЬ",
                        help="сделать заготовку эталона рядом с этой записью")
    parser.add_argument("--folder", default="",
                        help="где искать эталоны (по умолчанию — папка результатов)")
    parser.add_argument("--only", default="",
                        help="какие варианты гонять, через запятую")
    parser.add_argument("--list", action="store_true", help="показать варианты и выйти")
    parser.add_argument("--models", action="store_true",
                        help="проверить, какие модели голосов вообще скачиваются")
    args = parser.parse_args()

    if args.models:
        return check_models()

    if args.list:
        for name, changes in VARIANTS.items():
            print(f"{name:24} {changes}")
        return 0

    settings = Settings.load()

    if args.new:
        wav = Path(args.new).expanduser()
        made = make_ref(wav)
        print(f"Эталон заготовлен: {made}")
        print("Поправьте его руками — имена и слова — и запустите замер без --new.")
        return 0

    folder = Path(args.folder).expanduser() if args.folder else settings.output_path
    refs = refs_in(folder)
    if not refs:
        print(f"В папке {folder} нет ни одного файла *.ref.txt.")
        print("Сделайте первый:  python tools/bench.py --new <запись.wav>")
        return 2

    picked = [n.strip() for n in args.only.split(",") if n.strip()] or list(VARIANTS)
    unknown = [n for n in picked if n not in VARIANTS]
    if unknown:
        raise SystemExit(f"нет таких вариантов: {', '.join(unknown)}")

    totals: dict[str, list] = {name: [] for name in picked}
    for ref_path in refs:
        wav = ref_path.with_name(ref_path.name[: -len(".ref.txt")] + ".wav")
        if not wav.exists():
            print(f"— {ref_path.name}: рядом нет .wav, пропускаю")
            continue
        reference = read_ref(ref_path)
        if not reference:
            print(f"— {ref_path.name}: эталон пуст, пропускаю")
            continue
        length = media.probe(str(wav)).duration or 0.0
        print(f"\n{wav.name}  ({len(reference)} реплик, "
              f"{int(length // 60)}:{int(length % 60):02d})")
        for name in picked:
            try:
                rows, spent = run_variant(wav, settings, VARIANTS[name])
            except Exception as exc:
                print(f"  {name:24} не сложилось: {exc}")
                continue
            got = metrics.score(reference, rows)
            speed = f"×{length / spent:.1f}" if spent > 0 and length else "—"
            print(f"  {name:24} {got.line()}  {spent / 60:.1f} мин {speed}")
            totals[name].append(got)

    print("\nИтого по всем записям:")
    for name in picked:
        rows = totals[name]
        if not rows:
            continue
        # Складываем ошибки и слова, а не средние проценты: иначе короткая
        # запись весит столько же, сколько часовая.
        words = sum(r.words for r in rows)
        bad = sum(r.wrong + r.missed + r.invented for r in rows)
        matched = sum(r.matched for r in rows)
        who = sum(r.wrong_who for r in rows)
        print(f"  {name:24} WER {bad / max(1, words) * 100:5.1f}%  "
              f"WDER {who / max(1, matched) * 100:5.1f}%  "
              f"({len(rows)} записей, {words} слов)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
