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
AUDITS_DIR = PROJECT_ROOT / "audits"
JOBS_DIR = LAUNCH_PULT_ROOT / "jobs" / "analysis"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
# Что разрешаем оркестру в фоне: чтение, веб, запуск субагентов, запись файла и
# playwright (браузер) для цен/контента за JS. Bash НЕ включён — деструктив закрыт.
ALLOWED_TOOLS = os.environ.get(
    "ANALYSIS_ALLOWED_TOOLS",
    "Read Write Edit Glob Grep WebFetch WebSearch Task mcp__playwright",
).split()
# Свой MCP-конфиг для фоновых задач: playwright в ИЗОЛИРОВАННОМ headless-профиле,
# чтобы не ловить lock общего профиля с основной сессией (тогда цены идут 🟢, не 🟡).
ANALYSIS_MCP_CONFIG = LAUNCH_PULT_ROOT / "mcp-analysis.json"
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


def _build_competitor_prompt(targets: str, focus: str, out_path: Path) -> str:
    focus_line = focus.strip() or "цены/тарифы, форматы обучения, длительность, оффер, гарантии возврата"
    return (
        "Ты — аналитик ниши онлайн-обучения ИИ и ИТ. Задача: сравнить конкурентов и "
        "собрать результат ОТДЕЛЬНЫМ ФАЙЛОМ.\n\n"
        f"Конкуренты / источники: {targets}\n"
        f"Ось сравнения: {focus_line}\n\n"
        "Как работать:\n"
        "0. Если среди источников есть ссылка на Google Документ (docs.google.com/document/…) — "
        "это ОПОРНЫЙ материал от заказчика. Прочитай его текст через WebFetch по адресу экспорта: "
        "замени хвост ссылки `/edit…` на `/export?format=txt` и открой. Используй содержимое как "
        "источник наравне с сайтами (но факты всё равно сверяй). Если документ не открывается "
        "(нет доступа) — в результате прямо напиши: «Google-док закрыт, дай доступ по ссылке».\n"
        "1. Открой скилл `.claude/skills/ai-competitors/SKILL.md` и иди по его чек-листу.\n"
        "2. Сначала пробуй WebFetch/WebSearch. Если страница даёт 403 или цена подгружается JS "
        "(Tilda и пр.) — открой её браузером playwright (mcp__playwright__browser_navigate, затем "
        "browser_snapshot) и сними цену оттуда. Браузер изолированный, lock-а профиля нет.\n"
        "3. Только если и браузер не достал — ПОМЕЧАЙ как пробел. Ничего не додумывай: нет данных "
        "— так и пиши. Каждый факт — со ссылкой на источник.\n\n"
        f"Сохрани результат инструментом Write строго в файл:\n{out_path}\n\n"
        "Структура файла (markdown):\n"
        "- Заголовок + дата + список разобранных конкурентов.\n"
        "- Сравнительная таблица: параметр × конкурент (по оси сравнения; пробелы помечай «—/нет данных»).\n"
        "- Раздел «Пробелы данных» — что не удалось достать и почему.\n"
        "- Вывод: сильные/слабые зоны конкурентов и возможности для позиционирования Zerocoder.\n\n"
        f"В конце ответа верни ровно одну строку: СОХРАНЕНО: {out_path}"
    )


def _build_audit_prompt(url: str, focus: str, out_path: Path) -> str:
    focus_line = focus.strip() or "оффер на первом экране, структура и логика прокрутки, конверсионные узлы, правдивость заявлений, битые/слабые ссылки"
    return (
        "Ты — аудитор посадочных страниц. Задача: проверить ЖИВУЮ страницу и собрать "
        "ранжированный список правок ОТДЕЛЬНЫМ ФАЙЛОМ.\n\n"
        f"Страница для аудита: {url}\n"
        f"Фокус проверки: {focus_line}\n\n"
        "Как работать:\n"
        "1. Запусти субагента `landing-auditor` (он зеркало landing-architect: не переписывает блоки, "
        "а диагностирует и приоритизирует) — передай ему URL и фокус.\n"
        "2. Открой страницу: сначала WebFetch. Если 403 или контент за JS — открой браузером "
        "playwright (mcp__playwright__browser_navigate + browser_snapshot) и смотри живой DOM. "
        "Браузер изолированный, lock-а нет. Что и так не отсмотрелось — помечай, не выдумывай.\n"
        "3. Ссылки проверяй осторожно: `curl`/WebFetch дают ложный 403 — помечай «проверить в браузере», "
        "а не сразу «битая».\n"
        "4. Каждая находка — с пруфом (цитата/блок со страницы), без догадок.\n\n"
        f"Сохрани результат инструментом Write строго в файл:\n{out_path}\n\n"
        "Структура файла (markdown):\n"
        "- Заголовок + URL + дата аудита.\n"
        "- Находки, РАНЖИРОВАННЫЕ по важности (критично / средне / мелочь): что не так + почему + пруф.\n"
        "- Отдельно — ссылки под проверку.\n"
        "- Раздел «Не отсмотрено» — что не удалось проверить и почему.\n"
        "- Короткое резюме: топ-3 правки с наибольшим эффектом.\n\n"
        f"В конце ответа верни ровно одну строку: СОХРАНЕНО: {out_path}"
    )


def _launch_job(chat_id: int, job_id: str, prompt: str, out_path: Path, kind: str, meta: dict) -> dict:
    """Общий запуск фоновой задачи-оркестра: детач `claude -p`, который сам пишет
    файл-результат в out_path. Возвращает job json (источник правды о задаче)."""
    if not claude_available():
        raise RuntimeError(
            f"Не найден '{CLAUDE_BIN}' — оркестр недоступен. Установлен ли Claude Code?"
        )
    if len(running_jobs()) >= MAX_CONCURRENT:
        raise RuntimeError("Уже считаю одну задачу. Дождись результата и запусти следующую.")

    now = dt.datetime.now().isoformat(timespec="seconds")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = JOBS_DIR / f"{job_id}.log"

    cmd = [CLAUDE_BIN, "-p", prompt]
    if ALLOWED_TOOLS:
        cmd += ["--allowedTools", *ALLOWED_TOOLS]
    # Изолированный playwright только для фоновой задачи (strict → грузим лишь его,
    # без коллизии с playwright основной сессии). Если конфига нет — работаем без него.
    if ANALYSIS_MCP_CONFIG.exists():
        cmd += ["--mcp-config", str(ANALYSIS_MCP_CONFIG), "--strict-mcp-config"]

    log_f = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        cmd, stdout=log_f, stderr=subprocess.STDOUT,
        start_new_session=True, env=os.environ.copy(), cwd=str(PROJECT_ROOT),
    )

    job = {
        "id": job_id,
        "kind": kind,
        "chat_id": chat_id,
        "status": "running",
        "pid": proc.pid,
        "out_path": str(out_path),
        "log_path": str(log_path),
        "started_at": now,
        "finished_at": None,
        "notified": False,
        **meta,
    }
    _write_job(job)
    return job


def create_competitor_job(chat_id: int, targets: str, focus: str = "") -> dict:
    """Фоновый разбор конкурентов через `claude -p` (оркестр пишет файл сам)."""
    job_id = uuid.uuid4().hex[:8]
    out_path = RESEARCH_DIR / f"{dt.date.today():%Y-%m-%d}_competitors-{job_id}_compare.md"
    prompt = _build_competitor_prompt(targets, focus, out_path)
    return _launch_job(
        chat_id, job_id, prompt, out_path, kind="competitors",
        meta={"targets": targets, "focus": focus},
    )


def create_audit_job(chat_id: int, url: str, focus: str = "") -> dict:
    """Фоновый аудит живой посадочной страницы через `claude -p` (landing-auditor)."""
    job_id = uuid.uuid4().hex[:8]
    out_path = AUDITS_DIR / f"{dt.date.today():%Y-%m-%d}_audit-{job_id}.md"
    prompt = _build_audit_prompt(url, focus, out_path)
    return _launch_job(
        chat_id, job_id, prompt, out_path, kind="audit",
        meta={"url": url, "focus": focus},
    )


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
