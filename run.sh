#!/usr/bin/env bash
# Консольный запуск:  ./run.sh запись.mp4 [ещё.mov …]
# Без аргументов открывает окно приложения.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Окружение не найдено. Сначала выполните: bash \"$DIR/install.sh\"" >&2
  exit 1
fi

cd "$DIR" || exit 1
if [[ $# -eq 0 ]]; then
  exec "$PY" -m app.main
else
  exec "$PY" -m app.cli "$@"
fi
