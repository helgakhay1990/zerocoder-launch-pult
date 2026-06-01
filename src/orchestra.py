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

# Точечные права записи: разрешаем только Write и Edit (создать/изменить файл).
# Bash в список НЕ входит — значит удаление (rm) и деструктивные команды закрыты.
# Чтение и запуск субагентов и так доступны по умолчанию. Пусто — режим read-only.
ALLOWED_TOOLS = os.environ.get("ORCHESTRA_ALLOWED_TOOLS", "Write Edit").split()


class OrchestraError(RuntimeError):
    pass


def claude_available() -> bool:
    return shutil.which(CLAUDE_BIN) is not None


def _run(cmd: list[str], cwd: Path, timeout: int):
    """Запустить claude и вернуть (код возврата, stdout, stderr).
    На таймауте — сразу OrchestraError (повторять смысла нет)."""
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise OrchestraError(
            f"Оркестр не ответил за {timeout} с. Сузь запрос или подними ORCHESTRA_TIMEOUT."
        )
    return r.returncode, r.stdout, r.stderr


def ask(
    prompt: str,
    cwd: Path | None = None,
    timeout: int | None = None,
    session_id: str | None = None,
) -> str:
    """Передать запрос оркестру и вернуть текстовый ответ.

    Если задан session_id — диалог с памятью: сначала пробуем продолжить сессию
    (--resume), при неудаче (сессии ещё нет / новый /reset) создаём её (--session-id).
    Без session_id — разовый запрос без памяти (как из CLI).

    Бросает OrchestraError, если claude не найден, упал или превысил таймаут.
    """
    if not prompt.strip():
        raise OrchestraError("Пустой запрос.")
    if not claude_available():
        raise OrchestraError(
            f"Не найден исполняемый файл '{CLAUDE_BIN}'. Установлен ли Claude Code?"
        )

    cwd = Path(cwd) if cwd else ORCHESTRA_CWD
    timeout = timeout or ASK_TIMEOUT
    base = [CLAUDE_BIN, "-p", prompt]
    if ALLOWED_TOOLS:
        base += ["--allowedTools", *ALLOWED_TOOLS]

    if session_id:
        # Продолжить существующую сессию; если её нет — создать с этим же id.
        rc, out, err = _run(base + ["--resume", session_id], cwd, timeout)
        if rc != 0:
            rc, out, err = _run(base + ["--session-id", session_id], cwd, timeout)
    else:
        rc, out, err = _run(base, cwd, timeout)

    if rc != 0:
        raise OrchestraError(f"claude вернул код {rc}: {(err or out).strip()[:500]}")
    return out.strip() or "Оркестр вернул пустой ответ."


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "Кратко: какие субагенты есть в оркестре?"
    print(ask(q))
