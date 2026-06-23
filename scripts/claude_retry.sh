#!/usr/bin/env bash
# Запуск claude (или любой команды) с авто-повтором при перегрузке ИИ.
# Anthropic иногда отдаёт 529 Overloaded («серверы заняты») — это временно.
# Повторяем до 3 раз с нарастающей паузой; на других ошибках выходим сразу.
#
# Использование:  claude_retry.sh <команда и аргументы...>
# Пример:         claude_retry.sh claude -p "..." --allowedTools Write

set -o pipefail
max=3
delay=30          # старт паузы, сек; растёт: 30, 60, 90
log="/tmp/claude_retry.$$"

for attempt in $(seq 1 "$max"); do
  "$@" 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  if [ "$rc" -eq 0 ]; then
    rm -f "$log"; exit 0
  fi
  # Перегрузка? — ждём и повторяем. Иначе — выходим с этой ошибкой.
  if grep -qiE "529|overloaded|at capacity|rate.?limit" "$log"; then
    if [ "$attempt" -lt "$max" ]; then
      echo "↻ ИИ перегружен (529), повтор ${attempt}/${max} через $((delay*attempt)) с..."
      sleep $((delay*attempt))
      continue
    fi
  fi
  rm -f "$log"; exit "$rc"
done

rm -f "$log"; exit 1
