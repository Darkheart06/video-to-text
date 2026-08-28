#!/usr/bin/env bash
# Собирает помощника захвата звука. Запускать на macOS с установленным Xcode
# или Command Line Tools:  bash capture/build.sh [куда_положить]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/../bin}"
BIN="$OUT/v2t-capture"
STAMP="$OUT/.v2t-capture.stamp"
FORCE=""
[[ "${2:-}" == "--force" || "${1:-}" == "--force" ]] && FORCE="1"

# macOS выдаёт разрешение на запись экрана конкретному файлу: пересобранный
# помощник для неё — другая программа, и разрешение приходится выдавать заново.
# Поэтому не пересобираем то, что не менялось.
SIGNATURE="$( { shasum -a 256 "$HERE/main.swift" "${BASH_SOURCE[0]}" 2>/dev/null;
                uname -m; xcrun swiftc --version 2>/dev/null | head -1; } | shasum -a 256 )"
if [[ -z "$FORCE" && -x "$BIN" && -f "$STAMP" ]] \
   && [[ "$(cat "$STAMP" 2>/dev/null)" == "$SIGNATURE" ]] \
   && "$BIN" check >/dev/null 2>&1; then
  echo "  ✓ помощник не изменился — оставляю прежний (разрешение macOS сохранится)"
  exit 0
fi

SWIFTC="$(command -v swiftc 2>/dev/null)"
[[ -n "$SWIFTC" ]] || SWIFTC="$(xcrun --find swiftc 2>/dev/null)"
[[ -n "$SWIFTC" && -x "$SWIFTC" ]] || {
  echo "Не найден swiftc. Нужен Xcode или: xcode-select --install" >&2; exit 1; }

mkdir -p "$OUT"
SDK="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null)"
[[ -n "$SDK" ]] || { echo "Не найден SDK macOS. Проверьте: xcode-select -p" >&2; exit 1; }

FRAMEWORKS=(-framework ScreenCaptureKit -framework AVFoundation
            -framework CoreAudio -framework CoreMedia -framework CoreGraphics)

build_one(){ # build_one <арка> <файл>
  "$SWIFTC" -O -swift-version 5 -sdk "$SDK" \
     -target "$1-apple-macos13.0" "${FRAMEWORKS[@]}" \
     -o "$2" "$HERE/main.swift" 2>&1
}

echo "Собираю помощника захвата…"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
HOST="$(uname -m)"

if ! build_one "$HOST" "$TMP/native" >"$TMP/log" 2>&1; then
  echo "  ✗ не собрался:" >&2; tail -12 "$TMP/log" >&2; exit 1
fi

# Второй архитектурой — чтобы образ подходил и Intel-макам, и Apple Silicon.
OTHER="x86_64"; [[ "$HOST" == "x86_64" ]] && OTHER="arm64"
if build_one "$OTHER" "$TMP/other" >>"$TMP/log" 2>&1 \
   && lipo -create "$TMP/native" "$TMP/other" -output "$BIN" 2>/dev/null; then
  echo "  ✓ универсальный: $(lipo -archs "$BIN")"
else
  cp "$TMP/native" "$BIN"
  echo "  ! только $HOST — на другой архитектуре запись работать не будет"
fi

chmod +x "$BIN"
# Постоянный идентификатор: с ним система показывает помощника по имени, а не
# как безымянный файл, и запись в списке разрешений остаётся узнаваемой.
codesign --force --sign - --identifier local.videototext.capture "$BIN" >/dev/null 2>&1
printf '%s' "$SIGNATURE" > "$STAMP"
echo "  ✓ $BIN"
"$BIN" check >/dev/null && echo "  ✓ запускается"
