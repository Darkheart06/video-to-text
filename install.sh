#!/usr/bin/env bash
# Установка приложения «Расшифровка записей» на macOS.
# Запуск:  bash install.sh          (спросит про большие загрузки)
#          bash install.sh --yes    (ничего не спрашивать)

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
APP_NAME="Расшифровка"
APP="$DIR/$APP_NAME.app"
ASSUME_YES=0
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && ASSUME_YES=1

bold(){ printf "\033[1m%s\033[0m\n" "$*"; }
ok(){   printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn(){ printf "  \033[33m!\033[0m %s\n" "$*"; }
err(){  printf "  \033[31m✗\033[0m %s\n" "$*"; }
step(){ printf "\n\033[1m%s\033[0m\n" "$*"; }

ask(){ # ask "вопрос" -> 0 если да
  [[ $ASSUME_YES == 1 ]] && return 0
  local reply
  read -r -p "  $1 [Y/n] " reply </dev/tty || return 1
  [[ -z "$reply" || "$reply" =~ ^[YyДд] ]]
}

bold "Установка приложения «Расшифровка записей»"
echo "Папка проекта: $DIR"

# --- 1. Система -------------------------------------------------------------
step "1. Проверка системы"
if [[ "$(uname -s)" != "Darwin" ]]; then
  warn "Скрипт рассчитан на macOS. На других системах поставьте зависимости вручную:"
  warn "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
fi
ARCH="$(uname -m)"
ok "macOS $(sw_vers -productVersion 2>/dev/null || echo '?'), архитектура $ARCH"

PY=""
for cand in python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver="$("$cand" -c 'import sys;print("%d%02d"%sys.version_info[:2])' 2>/dev/null || echo 0)"
    if [[ "$ver" -ge 309 ]]; then PY="$cand"; break; fi
  fi
done
if [[ -z "$PY" ]]; then
  err "Нужен Python 3.9 или новее."
  err "Установите: brew install python@3.12  (или скачайте с python.org)"
  exit 1
fi
ok "Python: $(command -v $PY) ($($PY -V 2>&1))"

# --- 2. ffmpeg --------------------------------------------------------------
step "2. ffmpeg (извлечение звука из видео)"
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg на месте: $(command -v ffmpeg)"
elif command -v brew >/dev/null 2>&1; then
  if ask "ffmpeg не найден. Установить через Homebrew?"; then
    brew install ffmpeg && ok "ffmpeg установлен" || err "Не удалось установить ffmpeg"
  else
    warn "Без ffmpeg приложение не сможет читать видеофайлы"
  fi
else
  err "ffmpeg не найден и Homebrew тоже."
  err "Поставьте Homebrew (https://brew.sh), затем: brew install ffmpeg"
fi

# --- 3. Python-окружение ----------------------------------------------------
step "3. Python-окружение и библиотеки"
if [[ ! -d "$VENV" ]]; then
  "$PY" -m venv "$VENV" || { err "Не удалось создать окружение"; exit 1; }
  ok "Создано окружение .venv"
else
  ok "Окружение .venv уже есть"
fi
"$VENV/bin/pip" install --quiet --upgrade pip setuptools wheel
echo "  Ставлю библиотеки (несколько минут при первом запуске)…"
if "$VENV/bin/pip" install --quiet -r "$DIR/requirements.txt"; then
  ok "Библиотеки установлены"
else
  err "Часть библиотек не встала. Подробности: $VENV/bin/pip install -r requirements.txt"
fi
"$VENV/bin/python" - <<'PY'
import importlib.util as u
have = lambda m: u.find_spec(m) is not None
print("  " + ("\033[32m✓\033[0m" if have("mlx_whisper") else "\033[33m!\033[0m") +
      " mlx-whisper " + ("готов (ускорение Apple Silicon)" if have("mlx_whisper") else "не установлен"))
print("  " + ("\033[32m✓\033[0m" if have("faster_whisper") else "\033[31m✗\033[0m") +
      " faster-whisper " + ("готов" if have("faster_whisper") else "не установлен"))
print("  " + ("\033[32m✓\033[0m" if have("sherpa_onnx") else "\033[31m✗\033[0m") +
      " sherpa-onnx " + ("готов (спикеры)" if have("sherpa_onnx") else "не установлен"))
print("  " + ("\033[32m✓\033[0m" if have("webview") else "\033[31m✗\033[0m") +
      " pywebview " + ("готов (окно)" if have("webview") else "не установлен"))
PY

# --- 4. Модели диаризации ---------------------------------------------------
step "4. Модели для разделения по спикерам (~35 МБ)"
if ( cd "$DIR" && "$VENV/bin/python" -m app.cli --download-models ); then
  ok "Модели на месте"
else
  warn "Не скачались — приложение попробует ещё раз при первом запуске"
fi

# --- 5. Языковая модель для саммари -----------------------------------------
step "5. Языковая модель для саммари"
echo "  Способов три: Ollama (проще всего), файл .gguf на диске, свой сервер"
echo "  с OpenAI API. Сейчас настроим Ollama — остальное включается в настройках."
echo

RAM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 8589934592) / 1073741824 ))
if   [[ $RAM_GB -ge 48 ]]; then SUGGEST="gemma4:26b"; SUGGEST_GB=19
elif [[ $RAM_GB -ge 20 ]]; then SUGGEST="gemma4:12b"; SUGGEST_GB=8
elif [[ $RAM_GB -ge 12 ]]; then SUGGEST="gemma4:e4b"; SUGGEST_GB=10
else                            SUGGEST="gemma4:e2b"; SUGGEST_GB=7
fi
OLLAMA_MODEL="${OLLAMA_MODEL:-$SUGGEST}"
ok "Оперативной памяти: ${RAM_GB} ГБ — по размеру подходит $OLLAMA_MODEL (~${SUGGEST_GB} ГБ)"

pull_model(){
  local base="$1"
  if [[ "$ARCH" == "arm64" ]] && ollama pull "${base}-mlx"; then
    ok "Скачана ${base}-mlx — сборка под Apple Silicon, работает быстрее обычной"
    return 0
  fi
  ollama pull "$base" && ok "Скачана $base"
}

if ! command -v ollama >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1 && ask "Ollama не найдена. Установить через Homebrew?"; then
    brew install --cask ollama && ok "Ollama установлена"
  else
    warn "Скачайте Ollama вручную: https://ollama.com/download"
    warn "Без неё транскрипт всё равно делается — не будет только саммари."
  fi
fi

if command -v ollama >/dev/null 2>&1; then
  if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    open -a Ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
      sleep 1
    done
  fi
  if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama отвечает"
    INSTALLED="$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | tr '\n' ' ')"
    if [[ -n "${INSTALLED// }" ]]; then
      ok "Уже установлены: $INSTALLED"
      ask "Скачать ещё и $OLLAMA_MODEL (~${SUGGEST_GB} ГБ)?" && pull_model "$OLLAMA_MODEL"
    elif ask "Моделей нет. Скачать $OLLAMA_MODEL (~${SUGGEST_GB} ГБ)?"; then
      pull_model "$OLLAMA_MODEL"
    else
      warn "Скачаете позже: ollama pull $OLLAMA_MODEL"
    fi
  else
    warn "Ollama не отвечает. Запустите приложение Ollama и повторите этот шаг."
  fi
fi

# --- 5b. Чтение файлов .gguf (по желанию) -----------------------------------
step "5b. Работа с файлом модели .gguf (по желанию)"
if "$VENV/bin/python" -c "import llama_cpp" >/dev/null 2>&1; then
  ok "llama-cpp-python уже установлена — файл .gguf можно указать в настройках"
elif ask "Поставить llama-cpp-python, чтобы указывать .gguf напрямую? (собирается 3–10 минут)"; then
  if CMAKE_ARGS="-DGGML_METAL=on" "$VENV/bin/pip" install llama-cpp-python; then
    ok "Готово — в настройках появится выбор файла модели"
  else
    err "Не собралась. Обычно помогает: xcode-select --install"
    warn "Это не мешает остальному: Ollama и внешний сервер работают без неё."
  fi
else
  warn "Пропущено. Поставить позже:"
  warn "  CMAKE_ARGS=\"-DGGML_METAL=on\" \"$VENV/bin/pip\" install llama-cpp-python"
fi

# --- 5c. Помощник захвата звука ---------------------------------------------
step "5c. Помощник захвата звука (для записи созвонов)"
if bash "$DIR/capture/build.sh" "$DIR/bin" 2>&1 | sed 's/^/  /'; then
  ok "готов"
else
  warn "не собрался — запись созвонов будет недоступна, остальное работает"
fi

# --- 6. Сборка .app ---------------------------------------------------------
step "6. Сборка приложения"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>Расшифровка записей</string>
  <key>CFBundleIdentifier</key><string>local.videototext.app</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Чтобы записывать ваш голос во время созвона и делать расшифровку.</string>
  <key>NSAudioCaptureUsageDescription</key>
  <string>Чтобы записывать голос собеседников во время созвона.</string>
</dict>
</plist>
PLIST

# Помощник захвата кладём внутрь приложения. macOS выдаёт разрешение на запись
# экрана не процессу, а программе: пока помощник лежал отдельным файлом, в
# списке разрешений появлялся не «Расшифровка», а тот, кто его запустил
# (python), и после каждого обновления доступ приходилось выдавать заново.
if [[ -x "$DIR/bin/v2t-capture" ]]; then
  cp -f "$DIR/bin/v2t-capture" "$APP/Contents/MacOS/v2t-capture"
  chmod +x "$APP/Contents/MacOS/v2t-capture"
fi

cat > "$APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
cd "$DIR" || exit 1
HELPER="\$(dirname "\$0")/v2t-capture"
[[ -x "\$HELPER" ]] && export V2T_HELPER="\$HELPER"
exec "$VENV/bin/python" -m app.main
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

if "$VENV/bin/python" "$DIR/tools/make_icon.py" "$DIR/.work/icon.png" >/dev/null 2>&1; then
  ICONSET="$DIR/.work/icon.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  for s in 16 32 64 128 256 512 1024; do
    sips -z $s $s "$DIR/.work/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
  done
  cp "$ICONSET/icon_32x32.png"     "$ICONSET/icon_16x16@2x.png"   2>/dev/null
  cp "$ICONSET/icon_64x64.png"     "$ICONSET/icon_32x32@2x.png"   2>/dev/null
  cp "$ICONSET/icon_256x256.png"   "$ICONSET/icon_128x128@2x.png" 2>/dev/null
  cp "$ICONSET/icon_512x512.png"   "$ICONSET/icon_256x256@2x.png" 2>/dev/null
  cp "$ICONSET/icon_1024x1024.png" "$ICONSET/icon_512x512@2x.png" 2>/dev/null
  rm -f "$ICONSET/icon_64x64.png" "$ICONSET/icon_1024x1024.png"
  if iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns" 2>/dev/null; then
    ok "Иконка собрана"
  else
    warn "Иконку собрать не удалось — приложение будет с системной"
  fi
  rm -rf "$ICONSET"
fi

# Подпись бандла — своя, без сертификата Apple. Смысл не в доверии, а в том,
# чтобы у приложения был устойчивый опознавательный знак: по нему система
# помнит выданные разрешения между запусками.
codesign --force --deep --sign - --identifier local.videototext.app "$APP" >/dev/null 2>&1 \
  && ok "Приложение подписано (своей подписью)" \
  || warn "Подписать не удалось — разрешение на запись экрана, возможно, придётся выдать заново"

touch "$APP"
ok "Готово: $APP"

# --- Итог -------------------------------------------------------------------
step "Всё установлено"
echo "  Запуск двойным кликом:  $APP"
echo "  Из терминала:           \"$DIR/run.sh\" запись.mp4"
echo
if ask "Скопировать приложение в /Applications?"; then
  rm -rf "/Applications/$APP_NAME.app"
  cp -R "$APP" "/Applications/" && ok "Лежит в /Applications" \
    || warn "Не удалось скопировать — перетащите .app в Программы вручную"
fi
echo
