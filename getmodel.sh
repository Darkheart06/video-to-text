#!/usr/bin/env bash
# Скачать модель распознавания и подготовить её к работе.
#
#   ./getmodel.sh antony66/whisper-large-v3-russian    скачать и перегнать
#   ./getmodel.sh --list                               что уже есть
#
# Питон берём из окружения приложения — системный не видит ни huggingface_hub,
# ни конвертер ctranslate2.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Окружение не найдено. Сначала выполните: bash \"$DIR/install.sh\"" >&2
  exit 1
fi

cd "$DIR" || exit 1
exec "$PY" tools/getmodel.py "$@"
