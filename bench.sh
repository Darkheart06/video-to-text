#!/usr/bin/env bash
# Замер качества разбора на своих записях.
#
#   ./bench.sh --new "~/Documents/Расшифровка записей/Созвон.wav"   заготовка эталона
#   ./bench.sh                                                      прогнать варианты
#   ./bench.sh --list                                               какие есть варианты
#   ./bench.sh --models                                             какие модели голосов качаются
#
# Тот же приём, что и в run.sh: берём питон из окружения приложения. Системный
# не годится — mlx-whisper и sherpa-onnx стоят только здесь, а «python» на
# macOS не существует вовсе, там только «python3».
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Окружение не найдено. Сначала выполните: bash \"$DIR/install.sh\"" >&2
  exit 1
fi

cd "$DIR" || exit 1
exec "$PY" tools/bench.py "$@"
