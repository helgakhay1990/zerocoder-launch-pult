"""Трекер запусков: читает _state.md по проектам и собирает статус-борд.

Парсер устойчив к двум форматам _state.md, которые ходят в проекте:
- пусковой конвейер ("# Состояние запуска: ...") — чек-боксы этапов,
  раздел "Открытые вопросы", раздел "Текущая стадия";
- недельный по оркестру ("# Состояние проекта: ...") — те же чек-боксы.

Внешних зависимостей нет — только стандартная библиотека.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Корень, по которому ищем _state.md. По умолчанию — рабочая папка Ai-homework
# (на два уровня выше: src/ -> launch-pult/ -> Ai-homework/).
DEFAULT_ROOT = Path(
    os.environ.get("WORKSPACE_ROOT", Path(__file__).resolve().parents[2])
)

_DONE_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]", re.MULTILINE)
_TODO_RE = re.compile(r"^\s*[-*]\s*\[\s\]", re.MULTILINE)
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_UPDATED_RE = re.compile(r"[Оо]бновлено[:*\s]*\**\s*(\d{4}-\d{2}-\d{2})")


@dataclass
class LaunchState:
    path: Path
    title: str
    updated: str | None
    done: int
    total: int
    open_questions: list[str] = field(default_factory=list)
    current_stage: str = ""

    @property
    def pct(self) -> int:
        return round(self.done / self.total * 100) if self.total else 0

    @property
    def project(self) -> str:
        # Имя проекта = имя родительской папки _state.md.
        return self.path.parent.name


def _section(text: str, header_keyword: str) -> str:
    """Вернуть тело раздела, заголовок которого содержит ключевое слово,
    до следующего заголовка уровня ##."""
    pattern = re.compile(
        rf"^#{{2,3}}\s+.*{re.escape(header_keyword)}.*$(.*?)(?=^#{{1,3}}\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _open_questions(text: str) -> list[str]:
    body = _section(text, "Открытые вопросы")
    if not body:
        return []
    items = []
    for line in body.splitlines():
        line = line.strip()
        # Берём только незакрытые пункты-вопросы, пропускаем подсказки в скобках.
        if line.startswith(("- [ ]", "* [ ]", "- ", "* ")):
            cleaned = re.sub(r"^[-*]\s*(\[\s\]\s*)?", "", line).strip()
            if cleaned and not cleaned.startswith("["):
                items.append(cleaned)
    return items


def parse_state(path: Path) -> LaunchState:
    text = path.read_text(encoding="utf-8")
    titles = _TITLE_RE.findall(text)
    title = titles[0] if titles else path.parent.name

    updated_m = _UPDATED_RE.search(text)
    updated = updated_m.group(1) if updated_m else None

    done = len(_DONE_RE.findall(text))
    total = done + len(_TODO_RE.findall(text))

    stage = _section(text, "Текущая стадия")
    # Берём первую содержательную строку стадии.
    stage_line = next((l.strip() for l in stage.splitlines() if l.strip()), "")

    return LaunchState(
        path=path,
        title=title,
        updated=updated,
        done=done,
        total=total,
        open_questions=_open_questions(text),
        current_stage=stage_line,
    )


def find_states(root: Path | None = None) -> list[LaunchState]:
    root = Path(root) if root else DEFAULT_ROOT
    states: list[LaunchState] = []
    for p in sorted(root.rglob("_state.md")):
        # Шаблоны и служебные копии не считаем запусками.
        if "template" in p.name.lower():
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        try:
            states.append(parse_state(p))
        except Exception as e:  # noqa: BLE001 — битый файл не должен ронять борд
            print(f"⚠️  Не разобран {p}: {e}")
    return states


def _bar(pct: int, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def render_board(states: list[LaunchState]) -> str:
    """Человекочитаемый статус-борд для терминала."""
    if not states:
        return "Запусков с _state.md не найдено."
    lines = ["", "ПУЛЬТ ЗАПУСКОВ", "=" * 60, ""]
    for s in states:
        lines.append(f"📂 {s.project}")
        lines.append(f"   {s.title}")
        upd = f"обновлён {s.updated}" if s.updated else "дата не указана"
        lines.append(f"   {_bar(s.pct)} {s.pct:>3}%  ({s.done}/{s.total})  · {upd}")
        if s.current_stage:
            lines.append(f"   стадия: {s.current_stage}")
        if s.open_questions:
            lines.append(f"   ⚠️  открытых вопросов: {len(s.open_questions)}")
            for q in s.open_questions[:3]:
                lines.append(f"       • {q}")
        lines.append("")
    return "\n".join(lines)


def render_board_md(states: list[LaunchState]) -> str:
    """Тот же борд в Markdown — для Telegram."""
    if not states:
        return "Запусков с `_state.md` не найдено."
    blocks = ["*Пульт запусков*", ""]
    for s in states:
        blocks.append(f"📂 *{s.project}* — {s.pct}% ({s.done}/{s.total})")
        if s.updated:
            blocks.append(f"   _обновлён {s.updated}_")
        if s.current_stage:
            blocks.append(f"   стадия: {s.current_stage}")
        if s.open_questions:
            blocks.append(f"   ⚠️ открытых вопросов: {len(s.open_questions)}")
        blocks.append("")
    return "\n".join(blocks)


if __name__ == "__main__":
    print(render_board(find_states()))
