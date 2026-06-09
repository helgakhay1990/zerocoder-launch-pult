"""Фоновые задачи аналитики (сравнение конкурентов) для бота «Пульт оркестра».

Отличие от montage_jobs: монтаж — детерминированный скрипт (whisper+OCR), а здесь
начинка это ОРКЕСТР. Задача запускает `claude -p` в папке проекта — он скиллом
`ai-competitors` + аналитиком собирает сравнение и САМ пишет результат в файл.
Бот ловит появление файла и досылает владельцу.

Почему фоном, а не через orchestra.ask_async: анализ с заходом в веб идёт минуты,
синхронный вызов упёрся бы в таймаут и повесил бы диалог. Здесь — отвязанный
процесс (переживает рестарт бота), готовность определяем по файлу-результату.

Своя папка jobs/analysis/ — чтобы наблюдатель монтажа (jobs/*.json) эти задачи
не подхватывал. Схема json и логика готовности — как в montage_jobs.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

# ── Пути ──────────────────────────────────────────────────────────────────────
LAUNCH_PULT_ROOT = Path(__file__).resolve().parents[1]      # .../launch-pult
PROJECT_ROOT = Path(__file__).resolve().parents[2]          # .../Ai-homework
RESEARCH_DIR = PROJECT_ROOT / "research"
JOBS_DIR = LAUNCH_PULT_ROOT / "jobs" / "analysis"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
# Что разрешаем оркестру в фоне: чтение, веб, запуск субагентов и запись файла.
# Bash НЕ включён — деструктивные команды закрыты.
ALLOWED_TOOLS = os.environ.get(
    "ANALYSIS_ALLOWED_TOOLS", "Read Write Edit Glob Grep WebFetch WebSearch Task"
).split()
MAX_CONCURRENT = 1


def _slug(text: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in text.lower()]
    return "".join(keep).strip("-")[:40] or "compare"


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _write_job(job: dict) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _job_path(job["id"]).write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_job(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def all_jobs() -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    out = [j for j in (_read_job(p) for p in JOBS_DIR.glob("*.json")) if j]
    return sorted(out, key=lambda j: j.get("started_at", ""), reverse=True)


def running_jobs() -> list[dict]:
    return [j for j in all_jobs() if j.get("status") == "running"]


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def claude_available() -> bool:
    return shutil.which(CLAUDE_BIN) is not None


def _build_prompt(targets: str, focus: str, out_path: Path) -> str:
    focus_line = focus.strip() or "цены/тарифы, форматы обучения, длительность, оффер, гарантии возврата"
    return (
        "Ты — аналитик ниши онлайн-обучения ИИ и ИТ. Задача: сравнить конкурентов и "
        "собрать результат ОТДЕЛЬНЫМ ФАЙЛОМ.\n\n"
        f"Конкуренты / источники: {targets}\n"
        f"Ось сравнения: {focus_line}\n\n"
        "Как работать:\n"
        "1. Открой скилл `.claude/skills/ai-competitors/SKILL.md` и иди по его чек-листу.\n"
        "2. Где возможно — посмотри страницы конкурентов (WebFetch/WebSearch). Цены на Tilda/за "
        "JS могут не достаться — это ПОМЕЧАЙ как пробел, не выдумывай.\n"
        "3. Каждый факт — со ссылкой на источник. Ничего не додумывай: нет данных — так и пиши.\n\n"
        f"Сохрани результат инструментом Write строго в файл:\n{out_path}\n\n"
        "Структура файла (markdown):\n"
        "- Заголовок + дата + список разобранных конкурентов.\n"
        "- Сравнительная таблица: параметр × конкурент (по оси сравнения; пробелы помечай «—/нет данных»).\n"
        "- Раздел «Пробелы данных» — что не удалось достать и почему.\n"
        "- Вывод: сильные/слабые зоны конкурентов и возможности для позиционирования Zerocoder.\n\n"
        f"В конце ответа верни ровно одну строку: СОХРАНЕНО: {out_path}"
    )


def create_competitor_job(chat_id: int, targets: str, focus: str = "") -> dict:
    """Запустить фоновый разбор конкурентов через `claude -p` (оркестр пишет файл сам)."""
    if not claude_available():
        raise RuntimeError(
            f"Не найден '{CLAUDE_BIN}' — оркестр недоступен. Установлен ли Claude Code?"
        )
    if len(running_jobs()) >= MAX_CONCURRENT:
        raise RuntimeError("Уже считаю один разбор. Дождись результата и запусти следующий.")

    job_id = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(timespec="seconds")
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESEARCH_DIR / f"{dt.date.today():%Y-%m-%d}_competitors-{job_id}_compare.md"
    log_path = JOBS_DIR / f"{job_id}.log"

    prompt = _build_prompt(targets, focus, out_path)
    cmd = [CLAUDE_BIN, "-p", prompt]
    if ALLOWED_TOOLS:
        cmd += ["--allowedTools", *ALLOWED_TOOLS]

    log_f = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        cmd, stdout=log_f, stderr=subprocess.STDOUT,
        start_new_session=True, env=os.environ.copy(), cwd=str(PROJECT_ROOT),
    )

    job = {
        "id": job_id,
        "kind": "competitors",
        "chat_id": chat_id,
        "targets": targets,
        "focus": focus,
        "status": "running",
        "pid": proc.pid,
        "out_path": str(out_path),
        "log_path": str(log_path),
        "started_at": now,
        "finished_at": None,
        "notified": False,
    }
    _write_job(job)
    return job


def _log_tail(log_path: str, limit: int = 1200) -> str:
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:  # noqa: BLE001
        return ""
    return text[-limit:]


def poll_jobs() -> list[dict]:
    """Сверить фоновые задачи анализа. Готовность — по появлению файла-результата.
    Возвращает завершённые и ещё не отправленные (done/failed, notified=False)."""
    finished: list[dict] = []
    for path in (JOBS_DIR.glob("*.json") if JOBS_DIR.exists() else []):
        job = _read_job(path)
        if not job:
            continue
        if job.get("status") == "running":
            pid = job.get("pid")
            if pid:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except (ChildProcessError, OSError):
                    pass
            out = Path(job.get("out_path", ""))
            done = out.exists() and out.stat().st_size > 0
            if done:
                job["status"] = "done"
            elif not pid_alive(pid):
                job["status"] = "failed"
                job["error_tail"] = _log_tail(job.get("log_path", ""))
            else:
                continue
            job["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
            _write_job(job)
        if job.get("status") in ("done", "failed") and not job.get("notified"):
            finished.append(job)
    return finished


def mark_notified(job_id: str) -> None:
    path = _job_path(job_id)
    job = _read_job(path)
    if job:
        job["notified"] = True
        _write_job(job)
