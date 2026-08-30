#!/usr/bin/env bash
# Собирает образ «Расшифровка-записей-<версия>.dmg» с установщиком внутри.
# Запускать на macOS из папки проекта:  bash packaging/make-dmg.sh [куда-положить]

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$ROOT/packaging"
VERSION="1.6.1"
VOLNAME="Расшифровка записей"
APPLET="Установить Расшифровку.app"
OUT_DIR="${1:-$ROOT/dist}"
DMG="$OUT_DIR/Расшифровка-записей-$VERSION.dmg"

ok(){   printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn(){ printf "  \033[33m!\033[0m %s\n" "$*"; }
die(){  printf "  \033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }
step(){ printf "\n\033[1m%s\033[0m\n" "$*"; }

[[ "$(uname -s)" == "Darwin" ]] || die "Собирать образ можно только на macOS"

BUILD="$(mktemp -d /tmp/v2t-dmg.XXXXXX)"
trap 'rm -rf "$BUILD"' EXIT
STAGE="$BUILD/stage"
mkdir -p "$STAGE" "$OUT_DIR"

printf "\033[1m%s\033[0m\n" "Сборка образа $VERSION"
echo "Проект: $ROOT"

# --- 1. Апплет-установщик ---------------------------------------------------
step "1. Собираю установщик"
osacompile -o "$STAGE/$APPLET" "$PKG/installer.applescript" \
  || die "osacompile не смог собрать апплет"
ok "апплет собран"

# --- 2. Начинка -------------------------------------------------------------
step "2. Кладу внутрь файлы приложения"
PAYLOAD="$STAGE/$APPLET/Contents/Resources/payload"
mkdir -p "$PAYLOAD"
cp -R "$ROOT/app" "$PAYLOAD/app"
cp -R "$ROOT/tools" "$PAYLOAD/tools"
cp "$ROOT/requirements.txt" "$PAYLOAD/"
cp "$PKG/install-payload.sh" "$PAYLOAD/"
chmod +x "$PAYLOAD/install-payload.sh"
find "$PAYLOAD" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
find "$PAYLOAD" -name '.DS_Store' -delete 2>/dev/null
ok "начинка на месте ($(du -sh "$PAYLOAD" | cut -f1))"

# --- 2b. Помощник захвата звука --------------------------------------------
step "2b. Собираю помощника захвата звука"
if bash "$ROOT/capture/build.sh" "$PAYLOAD/bin" 2>&1 | sed 's/^/  /'; then
  ok "помощник в начинке"
else
  warn "не собрался — запись созвонов в этом образе работать не будет"
fi

# --- 3. Иконка --------------------------------------------------------------
step "3. Рисую иконку"
ICON_PY=""
if python3 -c "import numpy" >/dev/null 2>&1; then
  ICON_PY="python3"
else
  python3 -m venv "$BUILD/iconenv" >/dev/null 2>&1 \
    && "$BUILD/iconenv/bin/pip" install --quiet numpy >/dev/null 2>&1 \
    && ICON_PY="$BUILD/iconenv/bin/python"
fi
if [[ -n "$ICON_PY" ]] && "$ICON_PY" "$ROOT/tools/make_icon.py" "$BUILD/icon.png" >/dev/null 2>&1; then
  ISET="$BUILD/icon.iconset"; mkdir -p "$ISET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$BUILD/icon.png" --out "$ISET/icon_${s}x${s}.png" >/dev/null 2>&1
    sips -z $((s*2)) $((s*2)) "$BUILD/icon.png" --out "$ISET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
  done
  if iconutil -c icns "$ISET" -o "$BUILD/icon.icns" >/dev/null 2>&1; then
    cp "$BUILD/icon.icns" "$STAGE/$APPLET/Contents/Resources/applet.icns"
    ok "иконка установщика заменена"
  else
    warn "iconutil не справился — останется стандартная иконка скрипта"
  fi
else
  warn "не удалось нарисовать иконку — останется стандартная"
fi

# --- 4. Опознавательные данные ----------------------------------------------
step "4. Прописываю имя и версию"
PL="$STAGE/$APPLET/Contents/Info.plist"
set_plist(){ /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PL" 2>/dev/null \
          || /usr/libexec/PlistBuddy -c "Add :$1 $3 $2" "$PL" >/dev/null 2>&1; }
set_plist CFBundleName "Установить Расшифровку" string
set_plist CFBundleDisplayName "Установить Расшифровку" string
set_plist CFBundleIdentifier "local.videototext.installer" string
set_plist CFBundleShortVersionString "$VERSION" string
set_plist CFBundleVersion "$VERSION" string
set_plist LSMinimumSystemVersion "12.0" string
set_plist NSHighResolutionCapable true bool
ok "готово"

# --- 5. Подпись -------------------------------------------------------------
step "5. Подписываю"
if codesign --force --deep --sign - "$STAGE/$APPLET" >/dev/null 2>&1; then
  codesign --verify --deep "$STAGE/$APPLET" >/dev/null 2>&1 \
    && ok "подпись на месте (самоподписанная)" \
    || warn "подпись есть, но проверка ворчит"
else
  warn "подписать не вышло — на чужом Mac придётся открывать через правый клик"
fi

# --- 6. Записка для того, кто откроет образ ----------------------------------
step "6. Кладу записку"
cp "$PKG/dmg-readme.txt" "$STAGE/Как установить.txt"
ok "записка на месте"

# --- 7. Сам образ -----------------------------------------------------------
step "7. Собираю образ"
rm -f "$DMG"
hdiutil create -volname "$VOLNAME" -srcfolder "$STAGE" -ov -quiet \
               -format UDZO -fs HFS+ "$DMG" \
  || die "hdiutil не смог собрать образ"
ok "образ собран"

# --- 8. Проверка ------------------------------------------------------------
step "8. Проверяю образ"
MNT="$BUILD/mnt"; mkdir -p "$MNT"
if hdiutil attach "$DMG" -mountpoint "$MNT" -nobrowse -quiet; then
  [[ -d "$MNT/$APPLET" ]] && ok "установщик внутри и открывается" || warn "установщика в образе нет"
  [[ -x "$MNT/$APPLET/Contents/Resources/payload/install-payload.sh" ]] \
    && ok "начинка на месте и запускаема" || warn "начинка потерялась"
  [[ -f "$MNT/$APPLET/Contents/Resources/payload/app/main.py" ]] \
    && ok "код приложения на месте" || warn "кода приложения нет"
  hdiutil detach "$MNT" -quiet
else
  warn "образ не смонтировался для проверки"
fi

printf "\n\033[1mГотово\033[0m\n"
echo "  $DMG"
echo "  $(du -h "$DMG" | cut -f1)"
echo
