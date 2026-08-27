"""Консольная версия: те же результаты без графического окна.

    python -m app.cli запись.mp4 [ещё.mov ...] [--no-summary] [--speakers 3]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import diarize, i18n, presets
from .pipeline import Runner
from .settings import WHISPER_MODELS, Settings


def _bar(fraction: float, width: int = 34) -> str:
    filled = int(round(fraction * width))
    return "█" * filled + "·" * (width - filled)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="video-to-text",
        description="Расшифровка аудио/видео с саммари и брифом. Всё локально.",
    )
    parser.add_argument("files", nargs="*", help="mp4, mov, webm, mp3, m4a, wav …")
    parser.add_argument("--out", help="папка для результатов")
    parser.add_argument("--model", choices=list(WHISPER_MODELS), help="модель Whisper")
    parser.add_argument("--language", help="код языка записи (ru, en …) или auto")
    parser.add_argument("--ui-lang", choices=["auto", "ru", "en"],
                        help="язык сообщений: auto — как в системе")
    parser.add_argument("--doc-lang", choices=["auto", "ru", "en"],
                        help="язык саммари и файлов: auto — как у сообщений")
    parser.add_argument("--speakers", type=int, help="сколько спикеров ожидается (0 = авто)")
    parser.add_argument("--no-speakers", action="store_true",
                        help="не разделять по спикерам")
    parser.add_argument("--preset", choices=list(presets.BUILTIN) + [presets.CUSTOM_KEY],
                        help="что готовить: meeting, estimate, note, interview, custom")
    parser.add_argument("--no-summary", action="store_true",
                        help="только транскрипт, без саммари")
    parser.add_argument("--llm", choices=["auto", "ollama", "gguf", "openai"],
                        help="как подключена языковая модель для саммари")
    parser.add_argument("--ollama-model", help="модель Ollama, например gemma4:12b-mlx")
    parser.add_argument("--gguf", help="путь к файлу модели .gguf")
    parser.add_argument("--api", help="адрес OpenAI-совместимого сервера, .../v1")
    parser.add_argument("--api-model", help="имя модели на этом сервере")
    parser.add_argument("--ctx", type=int, help="размер контекста модели")
    parser.add_argument("--check-llm", action="store_true",
                        help="проверить связь с языковой моделью и выйти")
    parser.add_argument("--download-models", action="store_true",
                        help="скачать модели диаризации и выйти")
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.ui_lang:
        settings["ui_language"] = args.ui_lang
    if args.doc_lang:
        settings["doc_language"] = args.doc_lang
    i18n.use(settings.get("ui_language", "auto"))
    if args.out:
        settings["output_dir"] = args.out
    if args.model:
        settings["whisper_model"] = args.model
    if args.language:
        settings["language"] = args.language
    if args.speakers is not None:
        settings["num_speakers"] = args.speakers
    if args.no_speakers:
        settings["diarization_enabled"] = False
    if args.preset:
        settings["preset"] = args.preset
    if args.no_summary:
        settings["summary_enabled"] = False
    if args.llm:
        settings["llm_backend"] = args.llm
    if args.ollama_model:
        settings["ollama_model"] = args.ollama_model
        settings["llm_backend"] = args.llm or "ollama"
    if args.gguf:
        settings["gguf_path"] = args.gguf
        settings["llm_backend"] = args.llm or "gguf"
    if args.api:
        settings["openai_base_url"] = args.api
        settings["llm_backend"] = args.llm or "openai"
    if args.api_model:
        settings["openai_model"] = args.api_model
    if args.ctx:
        settings["llm_num_ctx"] = args.ctx

    if args.check_llm:
        from . import llm

        result = llm.self_test(settings)
        if result["ok"]:
            print(f"✓ {result['backend']} отвечает: «{result['answer']}»")
            return 0
        print(f"✗ {result.get('error', 'не отвечает')}")
        return 1

    if args.download_models:
        diarize.download_models(lambda f, m: print(f"\r{_bar(f)} {m}", end="", flush=True))
        print()
        return 0

    if not args.files:
        parser.error("укажите хотя бы один файл")

    runner = Runner(settings)
    failed = 0

    for raw in args.files:
        path = Path(raw).expanduser().resolve()
        print(f"\n▶ {path.name}")
        job = runner.submit(str(path))
        last = ""
        while job.status in ("pending", "running"):
            line = f"\r{_bar(job.progress)} {job.progress * 100:5.1f}%  {job.message[:52]:<52}"
            if line != last:
                sys.stdout.write(line)
                sys.stdout.flush()
                last = line
            time.sleep(0.25)
        sys.stdout.write("\r" + " " * 110 + "\r")

        if job.status == "error":
            print(f"  ✗ Ошибка: {job.error}")
            failed += 1
            continue

        for warning in job.warnings:
            print(f"  ! {warning}")
        if job.speakers:
            names = ", ".join(
                f"{v['label']} ({v['seconds'] / 60:.0f} мин)"
                for v in job.speakers.values()
            )
            print(f"  Спикеры: {names}")
        print(f"  Готово за {job.meta.get('processed_at', '')}")
        for file_path in job.files.values():
            print(f"  · {file_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
