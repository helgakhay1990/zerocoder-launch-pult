"""Мост к оркестру субагентов: гоняет запрос через `claude -p` в папке проекта.

Используется и из CLI, и из Telegram-бота. Сам Claude Code не переписываем —
просто вызываем его в headless-режиме (-p / --print) в директории, где лежат
субагенты, knowledge-base и скиллы. Так бот отвечает «голосом» оркестра.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Папка, в которой запускаем claude -p. По умолчанию — рабочая папка Ai-homework
# (там CLAUDE.md оркестра, субагенты в .claude/agents и т.д.).
ORCHESTRA_CWD = Path(
    os.environ.get("WORKSPACE_ROOT", Path(__file__).resolve().parents[2])
)
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
ASK_TIMEOUT = int(os.environ.get("ORCHESTRA_TIMEOUT", "180"))


class OrchestraError(RuntimeError):
    pass


def claude_available() -> bool:
    return shutil.which(CLAUDE_BIN) is not None


def ask(prompt: str, cwd: Path | None = None, timeout: int | None = None) -> str:
    """Передать запрос оркестру и вернуть текстовый ответ.

    Бросает OrchestraError, если claude не найден, упал или превысил таймаут.
    """
    if not prompt.strip():
        raise OrchestraError("Пустой запрос.")
    if not claude_available():
        raise OrchestraError(
            f"Не найден исполняемый файл '{CLAUDE_BIN}'. Установлен ли Claude Code?"
        )

    cwd = Path(cwd) if cwd else ORCHESTRA_CWD
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout or ASK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise OrchestraError(
            f"Оркестр не ответил за {timeout or ASK_TIMEOUT} с. Попробуй сузить запрос."
        )

    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        raise OrchestraError(f"claude вернул код {result.returncode}: {err[:500]}")

    answer = result.stdout.strip()
    return answer or "Оркестр вернул пустой ответ."


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "Кратко: какие субагенты есть в оркестре?"
    print(ask(q))
