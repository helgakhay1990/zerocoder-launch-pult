"""Telegram-бот «Пульт оркестра».

Замкнут строго на один Telegram user ID (твой). Любой другой — игнор.
Команды:
    /start   — приветствие и подсказка
    /status  — статус-борд по _state.md (через tracker)
    /reset   — забыть контекст диалога, начать сессию заново
    любой текст — вопрос оркестру через claude -p (с памятью на чат)

Запуск:
    python src/bot.py
Перед запуском нужен .env (см. .env.example): TELEGRAM_BOT_TOKEN и ALLOWED_USER_ID.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from functools import wraps
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# tracker / orchestra лежат рядом, в той же папке src.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker  # noqa: E402
import orchestra  # noqa: E402

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("pult-bot")


def _load_env() -> tuple[str, int]:
    """Подтянуть .env из корня проекта (без сторонних библиотек) и вернуть
    токен и разрешённый user id."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed = os.environ.get("ALLOWED_USER_ID", "").strip()
    if not token:
        raise SystemExit("Нет TELEGRAM_BOT_TOKEN. Заполни .env (см. .env.example).")
    if not allowed.isdigit():
        raise SystemExit("Нет числового ALLOWED_USER_ID. Заполни .env.")
    return token, int(allowed)


ALLOWED_USER_ID = 0  # перезапишется в main()


def md_to_plain(text: str) -> str:
    """Лёгкая чистка Markdown оркестра для Telegram: убрать **, __, `,
    маркеры заголовков. Списки и переносы оставляем как есть."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **жирный**
    text = re.sub(r"__(.+?)__", r"\1", text)  # __подчёркнутый__
    text = re.sub(r"`([^`]+)`", r"\1", text)  # `код`
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # ## заголовок
    return text


def restricted(func):
    """Пускать только владельца. Остальным — короткий отказ в лог и в чат."""

    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if uid != ALLOWED_USER_ID:
            log.warning("Отказано в доступе: user_id=%s", uid)
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Этот бот приватный.",
                )
            return
        return await func(update, context)

    return wrapped


# Память диалога: на каждый чат — своя сессия claude. uuid детерминирован из
# chat_id и «эпохи»; /reset увеличивает эпоху → новая сессия, старый контекст забыт.
# Сессии claude хранятся на диске, поэтому память переживает перезапуск бота.
_SESSION_NS = uuid.NAMESPACE_URL
_epoch: dict[int, int] = {}


def _session_id(chat_id: int) -> str:
    epoch = _epoch.get(chat_id, 0)
    return str(uuid.uuid5(_SESSION_NS, f"pult-{chat_id}-{epoch}"))


async def _keep_typing(bot, chat_id: int, stop: asyncio.Event) -> None:
    """Держать индикатор «печатает…», пока оркестр думает (он живёт ~5 сек)."""
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:  # noqa: BLE001 — индикатор не критичен
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4)
        except asyncio.TimeoutError:
            pass


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Пульт оркестра на связи.\n\n"
        "• /status — статус всех запусков\n"
        "• /reset — забыть контекст и начать заново\n"
        "• просто напиши вопрос — отвечу через оркестр (помню контекст диалога)"
    )


@restricted
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _epoch[chat_id] = _epoch.get(chat_id, 0) + 1
    await update.message.reply_text("Контекст сброшен — начинаем диалог заново.")


@restricted
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    board = tracker.render_board_md(tracker.find_states())
    await update.message.reply_text(board, parse_mode="Markdown")


@restricted
async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Мгновенная обратная связь + живой индикатор «печатает». Вызов оркестра —
    # асинхронный, поэтому бот не висит и параллельно отвечает на другие сообщения.
    ack = await update.message.reply_text("Принял, работаю над задачей… 🛠")
    stop = asyncio.Event()
    typing = asyncio.create_task(_keep_typing(context.bot, chat_id, stop))
    try:
        answer = md_to_plain(
            await orchestra.ask_async(question, session_id=_session_id(chat_id))
        )
    except orchestra.OrchestraError as e:
        answer = f"⚠️ {e}"
    finally:
        stop.set()
        await typing

    try:
        await ack.delete()
    except Exception:  # noqa: BLE001 — не смогли убрать ack, не страшно
        pass

    # Telegram режет сообщения длиннее 4096 символов.
    for chunk in (answer[i : i + 4000] for i in range(0, len(answer), 4000)):
        await update.message.reply_text(chunk)


def main() -> None:
    global ALLOWED_USER_ID
    token, ALLOWED_USER_ID = _load_env()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ask))

    log.info("Пульт оркестра запущен. Владелец: %s", ALLOWED_USER_ID)
    app.run_polling()


if __name__ == "__main__":
    main()
