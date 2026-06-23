"""Фоновые задачи сборки ТЗ на монтаж (Montage-TZ) для бота «Пульт оркестра».

Зачем отдельный модуль: монтаж — это whisper + OCR на запись в часы, идёт 10–20 минут.
Через `claude -p` (orchestra) это гнать нельзя (таймаут 180 с + лишний прогон модели).
Поэтому бот зовёт детерминированный `montage_tz.py` напрямую — фоновым процессом.

Устойчивость к перезапуску бота:
  - каждая задача — это `jobs/<id>.json` (источник правды) + отвязанный процесс
    (start_new_session=True), который переживает уход бота;
  - наблюдатель (poll_jobs) сверяет живость pid и наличие файла-черновика, а не
    ждёт дочерний процесс — поэтому после рестарта бот подхватывает готовое.

Что НЕ делает: не принимает решений по монтажу и ничего не шлёт во внешние каналы.
На выходе — путь к черновику `.md`, который бот отдаёт владельцу на сверку.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

# ── Пути ──────────────────────────────────────────────────────────────────────
LAUNCH_PULT_ROOT = Path(__file__).resolve().parents[1]      # .../launch-pult
PROJECT_ROOT = Path(__file__).resolve().parents[2]          # .../Ai-homework
MONTAGE_SCRIPT = PROJECT_ROOT / "agent-assistant" / "skills" / "Montage-TZ" / "montage_tz.py"
REPORTS_DIR = PROJECT_ROOT / "agent-assistant" / "reports"
JOBS_DIR = LAUNCH_PULT_ROOT / "jobs"

# Один монтаж за раз: whisper тяжёлый на слабых машинах.
MAX_CONCURRENT = 1


def _slug(text: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in text.lower()]
    return "".join(keep).strip("-")[:40] or "efir"


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
    except Exception:  # noqa: BLE001 — битый json не должен ронять наблюдателя
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
    except PermissionError:  # процесс есть, но не наш — всё равно живой
        return True
    return True


def create_job(chat_id: int, source: str, windows: str, notes: str, theme: str | None = None) -> dict:
    """Запустить montage_tz.py в фоне (отвязанным процессом) и записать job json.

    source  — Kinescope video_id / ссылка / локальный путь (montage_tz сам разберёт).
    windows — '20m-50m,1h15m-1h40m' / 'auto' (авто-детект окон) / пусто (экранный слой не сканится).
    notes   — известные автором моменты (высокая уверенность), через ';'.
    """
    if len(running_jobs()) >= MAX_CONCURRENT:
        raise RuntimeError("Уже считаю одну запись. Дождись её готовности и запусти следующую.")

    job_id = uuid.uuid4().hex[:8]
    theme = _slug(theme) if theme else "efir"
    now = dt.datetime.now().isoformat(timespec="seconds")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"{dt.date.today():%Y-%m-%d}_{theme}-{job_id}_montazh-tz-draft.md"
    log_path = JOBS_DIR / f"{job_id}.log"

    cmd = [sys.executable, str(MONTAGE_SCRIPT), "--source", source,
           "--theme", theme, "--out", str(out_path)]
    if windows:
        cmd += ["--windows", windows]
    if notes:
        cmd += ["--notes", notes]
    # Если автор в заметках просит вычистить годы (на экране/слайдах) — включаем
    # поиск голых годов в OCR (--ocr-years). По умолчанию он выкл (шумит на таблицах),
    # но раз автор прямо назвал «года» — лучше показать кандидатов, чем пропустить.
    if notes and re.search(r"\bгод[аовуы]?\b", notes, re.IGNORECASE):
        cmd += ["--ocr-years"]

    # Отвязанный процесс: переживает уход бота. Лог — в файл, чтобы видеть причину падения.
    log_f = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 — держим открытым для процесса
    proc = subprocess.Popen(
        cmd, stdout=log_f, stderr=subprocess.STDOUT,
        start_new_session=True, env=os.environ.copy(), cwd=str(PROJECT_ROOT),
    )

    job = {
        "id": job_id,
        "chat_id": chat_id,
        "source": source,
        "windows": windows,
        "notes": notes,
        "theme": theme,
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
    """Свериться с состоянием фоновых задач. Возвращает задачи, которые завершились
    и ещё не были отправлены владельцу (status done/failed, notified=False).

    Логика завершения: процесс умер → смотрим, появился ли непустой файл-черновик.
    Есть файл → done; нет → failed (в job кладём хвост лога). Так работает и после
    рестарта бота, когда дочерний процесс уже не наш."""
    finished: list[dict] = []
    for path in (JOBS_DIR.glob("*.json") if JOBS_DIR.exists() else []):
        job = _read_job(path)
        if not job:
            continue
        if job.get("status") == "running":
            pid = job.get("pid")
            # Подобрать зомби, если это наш дочерний процесс: иначе он завершился, но
            # os.kill(pid,0) до reaping считает его живым. Не наш (после рестарта) —
            # ChildProcessError, его уже подберёт launchd.
            if pid:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except (ChildProcessError, OSError):
                    pass
            out = Path(job.get("out_path", ""))
            done = out.exists() and out.stat().st_size > 0
            if done:
                # Файл-черновик пишется в самом конце пайплайна → его наличие = готово.
                job["status"] = "done"
            elif not pid_alive(pid):
                job["status"] = "failed"
                job["error_tail"] = _log_tail(job.get("log_path", ""))
            else:
                continue  # ещё считает
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
