#!/bin/bash
# Рабочая часть установщика. Апплет вызывает её по одному шагу за раз:
#   install-payload.sh prepare | python | deps | ffmpeg | models | bundle | verify
# Каждый шаг можно безопасно повторять — уже сделанное пропускается.

set -uo pipefail

PAYLOAD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RT="$HOME/Library/Application Support/VideoToText"
LOG="$HOME/Library/Logs/Расшифровка-установка.log"
PY="$RT/python/bin/python3"
APP_NAME="Расшифровка"

PBS_REPO="astral-sh/python-build-standalone"
FF_REPO="eugeneware/ffmpeg-static"
PY_SERIES="3.12"

case "$(uname -m)" in
  arm64)  PBS_ARCH="aarch64"; FF_ARCH="arm64" ;;
  x86_64) PBS_ARCH="x86_64";  FF_ARCH="x64"   ;;
  *) echo "Неизвестный процессор: $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$(dirname "$LOG")"
log(){ printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*" >>"$LOG"; }
die(){ log "ОШИБКА: $*"; printf '%s\n' "$*" >&2; exit 1; }

# Куда класть приложение: обычно /Applications, но если туда нельзя — к себе.
target_apps(){
  if [[ -w /Applications ]]; then echo "/Applications"
  else mkdir -p "$HOME/Applications"; echo "$HOME/Applications"; fi
}

# Ссылка на нужный файл в последнем релизе GitHub.
# Намеренно без Python и jq: на чистой macOS их может не оказаться, а curl,
# grep и cut есть всегда.
release_asset(){ # release_asset <repo> <подстрока 1> [подстрока 2]
  local repo="$1" a="$2" b="${3:-}"
  curl -fsSL --connect-timeout 30 -H "User-Agent: video-to-text-installer" \
       "https://api.github.com/repos/$repo/releases/latest" 2>/dev/null \
    | tr ',' '\n' \
    | grep '"browser_download_url"' \
    | cut -d'"' -f4 \
    | grep -F -- "$a" \
    | { [[ -n "$b" ]] && grep -F -- "$b" || cat; } \
    | sort | tail -1
}

fetch(){ # fetch <url> <файл>
  log "качаю $1"
  # --no-progress-meter: полоска curl ушла бы в поток ошибок и попала в диалог
  curl -fL -sS --no-progress-meter --retry 3 --retry-delay 2 \
       --connect-timeout 30 -o "$2" "$1" \
    || die "Не удалось скачать: $1"
}

# ---------------------------------------------------------------- шаги

step_prepare(){
  log "=== установка начата, процессор $(uname -m), macOS $(sw_vers -productVersion) ==="
  mkdir -p "$RT/bin" "$RT/models" || die "Нет доступа к папке $RT"
  rm -rf "$RT/app"
  cp -R "$PAYLOAD/app" "$RT/app" || die "Не удалось скопировать файлы приложения"
  cp -R "$PAYLOAD/tools" "$RT/tools" 2>/dev/null
  cp "$PAYLOAD/requirements.txt" "$RT/" || die "Не найден список библиотек"
  if [[ -x "$PAYLOAD/bin/v2t-capture" ]]; then
    cp -f "$PAYLOAD/bin/v2t-capture" "$RT/bin/v2t-capture"
    chmod +x "$RT/bin/v2t-capture"
    log "помощник захвата звука на месте"
  else
    log "помощника захвата нет — запись созвонов будет недоступна"
  fi
  log "файлы приложения на месте"
}

step_python(){
  if [[ -x "$PY" ]] && "$PY" -c 'import sys' >/dev/null 2>&1; then
    log "python уже установлен: $("$PY" -V 2>&1)"
    return 0
  fi
  local url tgz
  # Имя файла выглядит как cpython-3.12.14+20260825-aarch64-apple-darwin-install_only.tar.gz,
  # поэтому ищем по двум кускам: серия версии и платформа.
  url="$(release_asset "$PBS_REPO" "cpython-${PY_SERIES}." \
                       "${PBS_ARCH}-apple-darwin-install_only.tar.gz")"
  [[ -n "$url" ]] || url="$(release_asset "$PBS_REPO" \
                       "${PBS_ARCH}-apple-darwin-install_only.tar.gz")"
  [[ -n "$url" ]] || die "Не нашёл сборку Python для $PBS_ARCH. Нужен интернет."
  tgz="$RT/python.tar.gz"
  fetch "$url" "$tgz"
  rm -rf "$RT/python"
  tar xzf "$tgz" -C "$RT" || die "Архив с Python не распаковался"
  rm -f "$tgz"
  [[ -x "$PY" ]] || die "Python распаковался не туда, чем ожидалось"
  log "python установлен: $("$PY" -V 2>&1)"
}

step_deps(){
  "$PY" -m pip install --quiet --upgrade pip setuptools wheel >>"$LOG" 2>&1
  log "ставлю библиотеки (долго)"
  "$PY" -m pip install --quiet -r "$RT/requirements.txt" >>"$LOG" 2>&1 \
    || die "Не установились библиотеки. Подробности в $LOG"
  log "библиотеки установлены"
}

step_ffmpeg(){
  local u
  for name in ffmpeg ffprobe; do
    if [[ -x "$RT/bin/$name" ]] && "$RT/bin/$name" -version >/dev/null 2>&1; then
      log "$name уже на месте"; continue
    fi
    u="$(release_asset "$FF_REPO" "${name}-darwin-${FF_ARCH}.gz")"
    if [[ -z "$u" ]]; then
      if command -v "$name" >/dev/null 2>&1; then
        log "$name не скачался, но есть системный — оставляю его"; continue
      fi
      die "Не удалось получить $name"
    fi
    fetch "$u" "$RT/bin/$name.gz"
    gunzip -f "$RT/bin/$name.gz" || die "Архив $name не распаковался"
    chmod +x "$RT/bin/$name"
    xattr -c "$RT/bin/$name" 2>/dev/null
    codesign --force --sign - "$RT/bin/$name" >>"$LOG" 2>&1
    "$RT/bin/$name" -version >/dev/null 2>&1 || die "$name не запускается"
    log "$name готов"
  done
}

step_models(){
  # Не через app.cli: его полоска прогресса засоряет журнал возвратами каретки.
  ( cd "$RT" && "$PY" -c \
      "from app import diarize; diarize.download_models(lambda f, m: None)" \
      >>"$LOG" 2>&1 ) \
    && log "модели спикеров готовы" \
    || log "модели спикеров не скачались — приложение возьмёт их при первом запуске"
}

step_bundle(){
  local apps app icon iconset
  apps="$(target_apps)"
  app="$apps/$APP_NAME.app"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" || die "Нет доступа к $apps"

  cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>Расшифровка записей</string>
  <key>CFBundleIdentifier</key><string>local.videototext.app</string>
  <key>CFBundleVersion</key><string>1.11.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Чтобы записывать ваш голос во время созвона и делать расшифровку.</string>
  <key>NSAudioCaptureUsageDescription</key>
  <string>Чтобы записывать голос собеседников во время созвона.</string>
  <key>NSCalendarsUsageDescription</key>
  <string>Чтобы показывать ближайшие созвоны и напоминать о них. Календарь читается на вашем компьютере, наружу ничего не уходит.</string>
  <key>NSCalendarsFullAccessUsageDescription</key>
  <string>Чтобы показывать ближайшие созвоны, напоминать о них и заводить новые прямо из приложения. Календарь читается на вашем компьютере, наружу ничего не уходит.</string>
</dict>
</plist>
PLIST

  # Помощник захвата — внутрь приложения: разрешение на запись экрана macOS
  # выдаёт программе, и так в списке разрешений появляется «Расшифровка», а не
  # безымянный файл или python, который её запустил.
  if [[ -x "$RT/bin/v2t-capture" ]]; then
    cp -f "$RT/bin/v2t-capture" "$app/Contents/MacOS/v2t-capture"
    chmod +x "$app/Contents/MacOS/v2t-capture"
  fi

  cat > "$app/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
RT="\$HOME/Library/Application Support/VideoToText"
cd "\$RT" || exit 1
# Finder запускает приложение с коротким PATH, без Homebrew — добавляем его
# сами, иначе ffmpeg «пропадает» именно при запуске по-человечески.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
HELPER="\$(dirname "\$0")/v2t-capture"
[[ -x "\$HELPER" ]] && export V2T_HELPER="\$HELPER"
exec "\$RT/python/bin/python3" -m app.main
LAUNCH
  chmod +x "$app/Contents/MacOS/launcher"

  icon="$RT/.work/icon.png"
  mkdir -p "$RT/.work"
  if "$PY" "$RT/tools/make_icon.py" "$icon" >>"$LOG" 2>&1; then
    iconset="$RT/.work/icon.iconset"
    rm -rf "$iconset"; mkdir -p "$iconset"
    for s in 16 32 128 256 512; do
      sips -z $s $s "$icon" --out "$iconset/icon_${s}x${s}.png" >/dev/null 2>&1
      sips -z $((s*2)) $((s*2)) "$icon" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$iconset" -o "$app/Contents/Resources/icon.icns" >>"$LOG" 2>&1 \
      && log "иконка собрана" || log "иконку собрать не вышло, будет системная"
    rm -rf "$iconset"
  fi

  codesign --force --deep --sign - --identifier local.videototext.app "$app" \
    >>"$LOG" 2>&1
  touch "$app"
  log "приложение собрано: $app"
  printf '%s\n' "$app"
}

step_verify(){
  ( cd "$RT" && "$PY" - <<'PY' >>"$LOG" 2>&1
import importlib.util as u
missing = [m for m in ("webview", "sherpa_onnx", "numpy") if u.find_spec(m) is None]
if not (u.find_spec("mlx_whisper") or u.find_spec("faster_whisper")):
    missing.append("движок распознавания")
if missing:
    raise SystemExit("не установлено: " + ", ".join(missing))
from app import media, llm, settings
print("проверка пройдена, ffmpeg:", media.tool("ffmpeg"))
PY
  ) || die "Проверка не прошла. Подробности в $LOG"
  log "проверка пройдена"
}

step_state(){   # что уже есть в системе — апплет спрашивает это перед финалом
  printf 'ollama=%s\n' "$(command -v ollama >/dev/null 2>&1 && echo yes || echo no)"
  printf 'brew=%s\n'   "$(command -v brew   >/dev/null 2>&1 && echo yes || echo no)"
  printf 'apps=%s\n'   "$(target_apps)"
  printf 'ram=%s\n'    "$(( $(sysctl -n hw.memsize 2>/dev/null || echo 8589934592) / 1073741824 ))"
}

case "${1:-}" in
  prepare) step_prepare ;;
  python)  step_python ;;
  deps)    step_deps ;;
  ffmpeg)  step_ffmpeg ;;
  models)  step_models ;;
  bundle)  step_bundle ;;
  verify)  step_verify ;;
  state)   step_state ;;
  *) die "Неизвестный шаг: ${1:-—}" ;;
esac
