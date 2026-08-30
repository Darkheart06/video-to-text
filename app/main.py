"""Точка входа приложения: нативное окно (pywebview) + мост в Python."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import (
    attach,
    diarize,
    edits,
    i18n,
    library,
    llm,
    media,
    people,
    presets,
    record,
    serve,
    voices,
)
from .pipeline import Runner
from .settings import WHISPER_MODELS, Settings

UI_FILE = Path(__file__).resolve().parent / "ui" / "index.html"

_window = None
_window_ready = threading.Event()


def _push(event: str, payload: dict) -> None:
    """Отправляет событие в интерфейс. Молча пропускает, если окно ещё не готово."""
    if _window is None or not _window_ready.is_set():
        return
    try:
        data = json.dumps({"event": event, "data": payload}, ensure_ascii=False)
        _window.evaluate_js(f"window.__bridge && window.__bridge({data})")
    except Exception:
        pass


class Api:
    def __init__(self) -> None:
        self.settings = Settings.load()
        i18n.use(self.settings.get("ui_language", "auto"))
        self.runner = Runner(self.settings, listener=lambda job: _push("job", job.snapshot()))
        self.steno = record.Stenographer(self.settings, listener=_push)
        threading.Thread(target=self._watch_for_calls, daemon=True).start()

    # --- запись созвонов --------------------------------------------------

    def _watch_for_calls(self) -> None:
        """Замечает, что микрофон кем-то занят, и предлагает записать разговор.
        Спрашивает один раз за созвон и не пристаёт после отказа."""
        was_busy = False
        muted_until = 0.0
        while True:
            time.sleep(5)
            if not self.settings.get("record_autodetect", True):
                continue
            try:
                busy = record.mic_busy()
            except Exception:
                continue
            if (busy and not was_busy and not self.steno.is_active()
                    and time.time() >= muted_until):
                _push("call-detected", {})
                muted_until = time.time() + 900
            was_busy = busy

    def rec_state(self) -> dict | None:
        session = self.steno.session
        return self.steno.snapshot_of(session) if session else None

    def rec_permissions(self) -> dict:
        return {**record.permissions(), "helper": record.helper_ready()}

    def rec_request(self) -> dict:
        return record.request_permissions()

    def rec_start(self, title: str = "", preset: str = "", mode: str = "call") -> dict:
        try:
            return {"ok": True,
                    "session": self.steno.start(title or "", preset or "", mode or "call")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def rec_people(self, names: list) -> dict | None:
        return self.steno.set_people([str(x) for x in (names or [])])

    def rec_tag(self, index: int, name: str = "") -> dict | None:
        try:
            return self.steno.tag(int(index), name or "")
        except Exception:
            return None

    def rec_name_voice(self, key: str, name: str = "") -> dict | None:
        """Имя голосу, который приложение различило по ходу разговора."""
        try:
            return self.steno.rename_voice(str(key or ""), name or "")
        except Exception:
            return None

    def rec_stop(self) -> dict | None:
        threading.Thread(target=self.steno.stop, daemon=True).start()
        return self.rec_state()

    def rec_cancel(self) -> bool:
        self.steno.cancel()
        return True

    def rec_snooze(self) -> bool:
        return True

    def open_privacy(self, pane: str = "screen") -> bool:
        """Открывает нужный раздел системных настроек и выводит окно вперёд.

        Ссылок две: у свежих macOS свой адрес, у прежних — старый. Пробуем по
        очереди, чтобы клик срабатывал с первого раза на любой системе.
        """
        anchor = {"screen": "Privacy_ScreenCapture",
                  "microphone": "Privacy_Microphone"}.get(pane, "Privacy_ScreenCapture")
        urls = [
            f"x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?{anchor}",
            f"x-apple.systempreferences:com.apple.preference.security?{anchor}",
        ]
        for url in urls:
            try:
                if subprocess.run(["open", url], check=False,
                                  capture_output=True, timeout=10).returncode == 0:
                    # Окно настроек любит открыться за нашим — поднимаем его.
                    subprocess.run(
                        ["osascript", "-e",
                         'tell application "System Settings" to activate'],
                        check=False, capture_output=True, timeout=10)
                    return True
            except Exception:
                continue
        return False

    # --- окружение --------------------------------------------------------

    def environment(self) -> dict:
        import importlib.util

        def has(mod: str) -> bool:
            return importlib.util.find_spec(mod) is not None

        return {
            # Именно media.tool, а не PATH: у установленной копии ffmpeg лежит
            # внутри приложения и в PATH его нет.
            "ffmpeg": bool(media.tool("ffmpeg")),
            "mlx": has("mlx_whisper"),
            "faster": has("faster_whisper"),
            "sherpa": has("sherpa_onnx"),
            "diar_models": diarize.models_ready(),
            "platform": f"{platform.system()} {platform.machine()}",
            "whisper_models": list(WHISPER_MODELS),
            "output_dir": str(self.settings.output_path),
            # Порт локального сервера: окно проигрывает звук и видео записи
            # через него — из file:// WebKit медиа с диска не отдаёт.
            "media_port": self._media_port(),
            # Что можно записывать с экрана: весь экран или одно приложение.
            "apps": record.running_apps() if record.helper_ready() else [],
            **llm.probe(self.settings),
        }

    def _media_port(self) -> int:
        try:
            return serve.start(self.settings.library_paths)
        except Exception:
            return 0

    def test_llm(self, values: dict | None = None) -> dict:
        """Кнопка «Проверить связь»: пробует настройки, не сохраняя их."""
        probe_settings = dict(self.settings)
        probe_settings.update(values or {})
        return llm.self_test(probe_settings)

    def get_settings(self) -> dict:
        return dict(self.settings)

    def save_settings(self, values: dict) -> dict:
        for key, value in (values or {}).items():
            if key in self.settings:
                current = self.settings[key]
                if isinstance(current, bool):
                    value = bool(value)
                elif isinstance(current, int) and not isinstance(current, bool):
                    value = int(value)
                elif isinstance(current, float):
                    value = float(value)
                self.settings[key] = value
        self.settings.save()
        i18n.use(self.settings.get("ui_language", "auto"))
        return dict(self.settings)

    def prepare_models(self) -> dict:
        """Догружает модели диаризации по кнопке из интерфейса."""
        try:
            diarize.download_models(
                lambda frac, msg: _push("models", {"progress": frac, "message": msg})
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # --- работа с файлами -------------------------------------------------

    def choose_files(self) -> list[str]:
        import webview

        from .media import SUPPORTED_EXT

        patterns = ";".join(f"*{e}" for e in sorted(SUPPORTED_EXT))
        result = _window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=(f"{i18n.t('app.files_media')} ({patterns})",
                        i18n.t("app.files_all")),
        )
        return list(result or [])

    def choose_gguf_file(self) -> str:
        import webview

        result = _window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=(i18n.t("app.files_gguf"), i18n.t("app.files_all")),
        )
        return str(result[0]) if result else ""

    def choose_output_dir(self) -> str:
        import webview

        result = _window.create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            self.settings["output_dir"] = str(result[0])
            self.settings.save()
            return str(result[0])
        return ""

    def start(self, paths: list[str], preset: str = "") -> list[dict]:
        return [self.runner.submit(p, preset).snapshot() for p in (paths or [])]

    # --- архив разобранных записей ---------------------------------------

    def library(self, query: str = "") -> dict:
        """Список всего, что уже разобрано, — для панели слева."""
        try:
            items = library.entries(self.settings.library_paths, query or "",
                                    self.settings.ui_lang)
        except Exception as exc:
            return {"items": [], "error": str(exc)}
        return {"items": items, "dir": str(self.settings.output_path)}

    def library_open(self, entry_id: str) -> dict | None:
        return library.snapshot(self.settings.library_paths, entry_id,
                                self.settings.ui_lang)

    def edit_summary(self, job_id: str, key: str, markdown: str) -> dict:
        """Заменяет раздел саммари — и в файлах, и в открытой карточке.

        Работает одинаково для только что разобранной записи и для открытой из
        архива: и там, и там правится один и тот же result.json.
        """
        job = self.runner.get(job_id)
        result = (job.files or {}).get("result") if job else None
        if not result:
            snapshot = library.snapshot(self.settings.library_paths, job_id,
                                        self.settings.ui_lang)
            result = (snapshot or {}).get("files", {}).get("result")
        if not result:
            return {"ok": False, "error": i18n.t("app.no_recording")}

        try:
            fresh = edits.apply(result, key, markdown or "", self.settings.doc_lang)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if job:
            job.summary_sections = fresh["sections"]
            job.summary_md = fresh["markdown"]
            if fresh["tables"]:
                job.files.setdefault(
                    "tables",
                    str(Path(result).parent
                        / Path(result).name.replace(
                            ".result.json", i18n.d("out.tables", self.settings.doc_lang))))
            elif "tables" in job.files:
                job.files.pop("tables")
        return {"ok": True, **fresh}

    def library_rename(self, entry_id: str, names: dict) -> dict | None:
        try:
            return library.rename(self.settings.library_paths, entry_id, names or {},
                                  self.settings.doc_lang)
        except Exception:
            return None

    # --- справочник людей и организаций ------------------------------------

    def people_list(self) -> dict:
        """Кого мы знаем и из каких команд — для настроек и для созвона."""
        try:
            return {"items": people.items(), "orgs": people.orgs()}
        except Exception as exc:
            return {"items": [], "orgs": [], "error": str(exc)}

    def people_add(self, name: str, org: str = "", role: str = "") -> dict:
        try:
            result = people.add(name, org, role)
            return {**result, "items": people.items(), "orgs": people.orgs()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def people_remove(self, name: str) -> dict:
        try:
            return {"ok": people.remove(str(name or "")),
                    "items": people.items(), "orgs": people.orgs()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # --- знакомые голоса ---------------------------------------------------

    def voices_list(self) -> dict:
        """Кого приложение уже узнаёт по голосу."""
        try:
            return {"items": voices.names()}
        except Exception as exc:
            return {"items": [], "error": str(exc)}

    def voices_learn(self, entry_id: str) -> dict:
        """Запоминает голоса из записи — только по команде человека.

        Автоматически этого не делается намеренно: если разделение ошиблось,
        приложение выучило бы ошибку навсегда.
        """
        try:
            snapshot = library.snapshot(self.settings.library_paths, entry_id,
                                        self.settings.ui_lang)
            result = (snapshot or {}).get("files", {}).get("result")
            if not result:
                return {"ok": False, "error": i18n.t("app.no_recording")}
            return voices.learn(result, int(self.settings["num_threads"]))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def voices_forget(self, name: str) -> dict:
        try:
            return {"ok": voices.forget(str(name or ""))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def library_delete(self, entry_id: str) -> dict:
        try:
            return library.delete(self.settings.library_paths, entry_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # --- документы к записи ------------------------------------------------

    def attachments(self, entry_id: str) -> dict:
        """Что приложено к записи: сметы, техзадания, письма."""
        path = library._path_of(self.settings.library_paths, str(entry_id or ""))
        if path is None:
            return {"items": []}
        return {"items": attach.items(path)}

    def attach_add(self, entry_id: str) -> dict:
        """Диалог выбора файлов и копия рядом с записью."""
        import webview

        path = library._path_of(self.settings.library_paths, str(entry_id or ""))
        if path is None:
            return {"ok": False, "error": i18n.t("lib.missing")}
        chosen = _window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                            file_types=(i18n.t("app.files_docs"),
                                                        i18n.t("app.files_all")))
        if not chosen:
            return {"ok": False, "cancelled": True}
        result = attach.add(path, [str(x) for x in chosen])
        return {**result, "items": attach.items(path)}

    def attach_preview(self, entry_id: str, name: str, size: int = 256) -> str:
        """Превью картинки, приложенной к записи, — картинкой в окно."""
        path = library._path_of(self.settings.library_paths, str(entry_id or ""))
        if path is None:
            return ""
        target = attach.folder_for(path) / Path(str(name or "")).name
        try:
            return attach.preview(target, int(size))
        except Exception:
            return ""

    def attach_remove(self, entry_id: str, name: str) -> dict:
        path = library._path_of(self.settings.library_paths, str(entry_id or ""))
        if path is None:
            return {"ok": False, "error": i18n.t("lib.missing")}
        result = attach.remove(path, str(name or ""))
        return {**result, "items": attach.items(path)}

    def resummarize(self, entry_id: str) -> dict:
        """Пересобрать саммари с учётом приложенных документов."""
        path = library._path_of(self.settings.library_paths, str(entry_id or ""))
        if path is None:
            return {"ok": False, "error": i18n.t("lib.missing")}
        try:
            job = self.runner.resummarize(str(path))
            return {"ok": True, "job": job.snapshot()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # --- корзина -----------------------------------------------------------

    def trash(self) -> dict:
        """Что лежит в корзине и сколько ему осталось там лежать."""
        try:
            days = int(self.settings.get("trash_days", 30))
            return {"items": library.trash(self.settings.library_paths, days,
                                           self.settings.ui_lang), "days": days}
        except Exception as exc:
            return {"items": [], "error": str(exc)}

    def trash_restore(self, trash_id: str) -> dict:
        try:
            return library.restore(self.settings.library_paths, str(trash_id or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def trash_purge(self, trash_id: str = "") -> dict:
        try:
            return library.purge(self.settings.library_paths, str(trash_id or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def presets(self) -> dict:
        """Профили для интерфейса плюс пример шаблона для своих правил."""
        lang = self.settings.doc_lang
        return {"items": presets.catalogue(lang),
                "current": self.settings.get("preset", presets.DEFAULT),
                "example": presets.custom_example(lang)}

    def job(self, job_id: str) -> dict | None:
        job = self.runner.get(job_id)
        return job.snapshot() if job else None

    def cancel(self, job_id: str) -> bool:
        return self.runner.cancel(job_id)

    def rename_speakers(self, job_id: str, names: dict) -> dict | None:
        return self.runner.rename_speakers(job_id, names or {})

    def reveal(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run(["open", "-R", str(target)], check=False)
            elif system == "Windows":
                subprocess.run(["explorer", "/select,", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target.parent)], check=False)
            return True
        except Exception:
            return False

    def copy(self, text: str) -> bool:
        """Запасной путь в буфер обмена.

        В окне WKWebView без строки меню Cmd+C и navigator.clipboard срабатывают
        не всегда, а pbcopy — всегда.
        """
        if not text:
            return False
        try:
            if platform.system() == "Darwin":
                done = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=10)
                return done.returncode == 0
            if platform.system() == "Windows":
                done = subprocess.run(["clip"], input=text.encode("utf-16-le"), timeout=10)
                return done.returncode == 0
            done = subprocess.run(["xclip", "-selection", "clipboard"],
                                  input=text.encode("utf-8"), timeout=10)
            return done.returncode == 0
        except Exception:
            return False

    def open_file(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False
        try:
            opener = {"Darwin": "open", "Windows": "start"}.get(platform.system(), "xdg-open")
            subprocess.run([opener, str(target)], check=False, shell=(opener == "start"))
            return True
        except Exception:
            return False


def run() -> int:
    global _window
    try:
        import webview
    except ImportError:
        print(i18n.t("app.no_pywebview"), file=sys.stderr)
        return 1

    api = Api()
    _window = webview.create_window(
        i18n.t("app.title"),
        str(UI_FILE),
        js_api=api,
        width=1180,
        height=820,
        min_size=(940, 640),
        # Без этого pywebview сам вставляет user-select: none, и текст в окне
        # нельзя ни выделить, ни скопировать. Что выделяется, а что нет,
        # решает уже наш css.
        text_select=True,
    )

    def on_loaded() -> None:
        _window_ready.set()

    _window.events.loaded += on_loaded
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
