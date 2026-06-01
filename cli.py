#!/usr/bin/env python3
"""Пульт оркестра — командная строка.

Две команды:
    python cli.py status            — статус-борд по всем _state.md
    python cli.py ask "вопрос..."   — задать вопрос оркестру через claude -p

Запуск без зависимостей (нужен только Python 3.9+ и установленный Claude Code
для команды ask). Для status хватает одного Python.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import tracker  # noqa: E402
import orchestra  # noqa: E402


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else None
    print(tracker.render_board(tracker.find_states(root)))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    question = " ".join(args.question).strip()
    if not question:
        print("Нужен текст вопроса: python cli.py ask \"...\"", file=sys.stderr)
        return 2
    try:
        print(orchestra.ask(question))
    except orchestra.OrchestraError as e:
        print(f"Ошибка оркестра: {e}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pult", description="Пульт оркестра запусков Zerocoder"
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="статус-борд по _state.md")
    p_status.add_argument("--root", help="корень поиска _state.md")
    p_status.set_defaults(func=cmd_status)

    p_ask = sub.add_parser("ask", help="задать вопрос оркестру (claude -p)")
    p_ask.add_argument("question", nargs="+", help="текст вопроса")
    p_ask.set_defaults(func=cmd_ask)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
