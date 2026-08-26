#!/usr/bin/env bash
# Собирает помощника захвата звука. Запускать на macOS с установленным Xcode
# или Command Line Tools:  bash capture/build.sh [куда_положить]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/../bin}"
BIN="$OUT/v2t-capture"

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
codesign --force --sign - "$BIN" >/dev/null 2>&1
echo "  ✓ $BIN"
"$BIN" check >/dev/null && echo "  ✓ запускается"
