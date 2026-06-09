"""Telegram-бот «Пульт оркестра».

Замкнут строго на один Telegram user ID (твой). Любой другой — игнор.

Управление — кнопочным меню (всегда внизу): «📊 Статус», «🔄 Сброс», «❓ Помощь».
Команды набирать не нужно, но они тоже работают:
    /start   — приветствие + показать меню
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

from telegram import BotCommand, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ── Кнопочное меню (всегда внизу, тапаешь вместо набора команд) ──
BTN_STATUS = "📊 Статус"
BTN_RESET = "🔄 Сброс"
BTN_HELP = "❓ Помощь"
BTN_MONTAGE = "🎬 ТЗ на монтаж"
BTN_COMPETE = "📊 Сравнить конкурентов"

MENU = ReplyKeyboardMarkup(
    [[BTN_MONTAGE], [BTN_COMPETE], [BTN_STATUS], [BTN_RESET, BTN_HELP]],
    resize_keyboard=True,
    is_persistent=True,
)

# Шаги диалога сборки ТЗ на монтаж
M_SOURCE, M_WINDOWS, M_NOTES = range(3)
# Шаги диалога сравнения конкурентов
C_TARGETS, C_FOCUS = range(10, 12)
# Что пользователь печатает, чтобы пропустить необязательный шаг
SKIP_WORDS = {"нет", "-", "—", "skip", "пропустить", "пропуск", "no"}
# Интервал опроса фоновых задач (сек) — общий для монтажа и анализа
MONTAGE_POLL_INTERVAL = 30
ANALYSIS_POLL_INTERVAL = 20

# tracker / orchestra лежат рядом, в той же папке src.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker  # noqa: E402
import orchestra  # noqa: E402
import montage_jobs  # noqa: E402
import analysis_jobs  # noqa: E402

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
        "Меню снизу — тапай кнопку, команды набирать не нужно:\n"
        f"• {BTN_STATUS} — статус всех запусков\n"
        f"• {BTN_RESET} — забыть контекст и начать заново\n"
        f"• {BTN_HELP} — это сообщение\n\n"
        "Или просто напиши вопрос — отвечу через оркестр (помню контекст диалога).",
        reply_markup=MENU,
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


# ── Диалог сборки ТЗ на монтаж ───────────────────────────────────────────────
# Кнопка «🎬 ТЗ на монтаж» → спрашиваем источник → окна экрана → заметки автора →
# ставим фоновую задачу montage_tz.py. Бот не висит; черновик придёт файлом, когда
# пайплайн досчитает (его ловит наблюдатель _montage_watcher).


@restricted
async def montage_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if montage_jobs.running_jobs():
        await update.message.reply_text(
            "Уже считаю одну запись 🎬 Дождись её готовности — запущу следующую.",
            reply_markup=MENU,
        )
        return ConversationHandler.END
    context.user_data["montage"] = {}
    await update.message.reply_text(
        "🎬 Собираю ТЗ на монтаж.\n\n"
        "Шаг 1/3. Кинь источник записи — Kinescope video_id или ссылку.\n"
        "В любой момент: /cancel — отмена."
    )
    return M_SOURCE


@restricted
async def montage_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["montage"]["source"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 2/3. Окна показа экрана — где демонстрировали экран (для OCR).\n"
        "Формат: `20m-50m,1h15m-1h40m`.\n"
        "Если не знаешь или показа не было — напиши «нет» (отсканирую без экранного слоя).",
        parse_mode="Markdown",
    )
    return M_WINDOWS


@restricted
async def montage_windows(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["montage"]["windows"] = "" if text.lower() in SKIP_WORDS else text
    await update.message.reply_text(
        "Шаг 3/3. Что знаешь точно? Известные моменты пойдут в черновик как высокоуверенная "
        "затравка.\n"
        "По пункту в строке, можно с таймкодом: `35:00 цена 990 на слайде`.\n"
        "Если нечего добавить — напиши «нет».",
        parse_mode="Markdown",
    )
    return M_NOTES


@restricted
async def montage_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data.get("montage", {})
    notes = "" if text.lower() in SKIP_WORDS else re.sub(r"\n+", "; ", text)
    try:
        job = montage_jobs.create_job(
            chat_id=update.effective_chat.id,
            source=data.get("source", ""),
            windows=data.get("windows", ""),
            notes=notes,
        )
    except Exception as e:  # noqa: BLE001 — показываем причину пользователю
        await update.message.reply_text(f"⚠️ Не запустил: {e}", reply_markup=MENU)
        return ConversationHandler.END

    win_txt = data.get("windows") or "без окон экрана (скан всей записи)"
    notes_txt = notes or "—"
    await update.message.reply_text(
        "Принял, считаю в фоне ⏳\n\n"
        f"• Источник: {data.get('source')}\n"
        f"• Окна экрана: {win_txt}\n"
        f"• Заметки автора: {notes_txt}\n\n"
        "whisper по записи в часы идёт ~10–20 минут. Пришлю черновик файлом, когда досчитаю. "
        "Можно продолжать переписку — бот не висит.",
        reply_markup=MENU,
    )
    context.user_data.pop("montage", None)
    return ConversationHandler.END


@restricted
async def montage_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("montage", None)
    await update.message.reply_text("Отменил сборку ТЗ.", reply_markup=MENU)
    return ConversationHandler.END


# ── Диалог сравнения конкурентов ─────────────────────────────────────────────
# Кнопка «📊 Сравнить конкурентов» → спрашиваем кого сравнить → ось сравнения →
# ставим фоновую задачу: claude -p (скилл ai-competitors) собирает таблицу + вывод
# и пишет файл. Бот не висит; результат придёт файлом (ловит _analysis_watcher).


@restricted
async def compete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if analysis_jobs.running_jobs():
        await update.message.reply_text(
            "Уже считаю один разбор 📊 Дождись результата — запущу следующий.",
            reply_markup=MENU,
        )
        return ConversationHandler.END
    context.user_data["compete"] = {}
    await update.message.reply_text(
        "📊 Сравниваю конкурентов.\n\n"
        "Шаг 1/2. Кого сравнить? Кинь список — ссылки на школы/курсы или названия "
        "через запятую.\n"
        "Например: skillbox.ru/нейросети, eduson.academy, нетология ИИ.\n"
        "В любой момент: /cancel — отмена."
    )
    return C_TARGETS


@restricted
async def compete_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["compete"]["targets"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 2/2. По какой оси сравнивать? Можно через запятую.\n"
        "Например: цены тарифов, формат, длительность, оффер, гарантии возврата.\n"
        "Если не уверена — напиши «нет», возьму стандартную ось.",
    )
    return C_FOCUS


@restricted
async def compete_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data.get("compete", {})
    focus = "" if text.lower() in SKIP_WORDS else text
    try:
        job = analysis_jobs.create_competitor_job(
            chat_id=update.effective_chat.id,
            targets=data.get("targets", ""),
            focus=focus,
        )
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Не запустил: {e}", reply_markup=MENU)
        return ConversationHandler.END

    await update.message.reply_text(
        "Принял, собираю разбор в фоне ⏳\n\n"
        f"• Кого: {data.get('targets')}\n"
        f"• Ось: {focus or 'стандартная (цены/формат/длительность/оффер/гарантии)'}\n\n"
        "Оркестр заходит на страницы конкурентов — это пара минут. Пришлю файлом со "
        "сравнением и выводом. Можно продолжать переписку — бот не висит.",
        reply_markup=MENU,
    )
    context.user_data.pop("compete", None)
    return ConversationHandler.END


@restricted
async def compete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("compete", None)
    await update.message.reply_text("Отменил разбор конкурентов.", reply_markup=MENU)
    return ConversationHandler.END


async def _analysis_watcher(app: Application) -> None:
    """Фоновая петля: сверяет задачи анализа и досылает готовый файл сравнения
    (или причину падения). Источник правды — jobs/analysis/<id>.json."""
    while True:
        try:
            for job in analysis_jobs.poll_jobs():
                chat_id = job.get("chat_id")
                if not chat_id:
                    analysis_jobs.mark_notified(job["id"])
                    continue
                try:
                    if job["status"] == "done":
                        out = Path(job["out_path"])
                        with out.open("rb") as fh:
                            await app.bot.send_document(
                                chat_id=chat_id,
                                document=fh,
                                filename=out.name,
                                caption=(
                                    "✅ Разбор конкурентов готов.\n"
                                    "Сравнение + вывод. Факты со ссылками; где пробел — помечено. "
                                    "Проверь перед использованием."
                                ),
                            )
                    else:
                        tail = job.get("error_tail", "") or "лог пуст"
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=f"⚠️ Разбор не собрался (кого: {job.get('targets')}).\n\n"
                                 f"Хвост лога:\n{tail[-1000:]}",
                        )
                    analysis_jobs.mark_notified(job["id"])
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось отправить разбор job=%s", job.get("id"))
        except Exception:  # noqa: BLE001
            log.exception("Сбой в наблюдателе анализа")
        await asyncio.sleep(ANALYSIS_POLL_INTERVAL)


async def _montage_watcher(app: Application) -> None:
    """Фоновая петля: раз в MONTAGE_POLL_INTERVAL сверяет фоновые задачи монтажа и
    досылает владельцу готовый черновик (или причину падения). Переживает рестарт —
    источник правды это jobs/<id>.json, а не дочерний процесс."""
    while True:
        try:
            for job in montage_jobs.poll_jobs():
                chat_id = job.get("chat_id")
                if not chat_id:
                    montage_jobs.mark_notified(job["id"])
                    continue
                try:
                    if job["status"] == "done":
                        out = Path(job["out_path"])
                        with out.open("rb") as fh:
                            await app.bot.send_document(
                                chat_id=chat_id,
                                document=fh,
                                filename=out.name,
                                caption=(
                                    "✅ Черновик ТЗ на монтаж готов.\n"
                                    "Это черновик на сверку — не финал монтажёру. "
                                    "Дальше: video-edit-assistant → сверка с автором."
                                ),
                            )
                    else:
                        tail = job.get("error_tail", "") or "лог пуст"
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=f"⚠️ Сборка ТЗ упала (источник: {job.get('source')}).\n\n"
                                 f"Хвост лога:\n{tail[-1000:]}",
                        )
                    montage_jobs.mark_notified(job["id"])
                except Exception:  # noqa: BLE001 — не смогли отправить, попробуем в следующий цикл
                    log.exception("Не удалось отправить результат монтажа job=%s", job.get("id"))
        except Exception:  # noqa: BLE001 — петля не должна умирать
            log.exception("Сбой в наблюдателе монтажа")
        await asyncio.sleep(MONTAGE_POLL_INTERVAL)


async def _post_init(app: Application) -> None:
    """Прописать команды в системное «/»-меню Telegram и поднять наблюдателя монтажа."""
    await app.bot.set_my_commands(
        [
            BotCommand("montage", "Собрать ТЗ на монтаж записи"),
            BotCommand("compete", "Сравнить конкурентов"),
            BotCommand("status", "Статус всех запусков"),
            BotCommand("reset", "Забыть контекст диалога"),
            BotCommand("start", "Меню и помощь"),
        ]
    )
    app.create_task(_montage_watcher(app))
    app.create_task(_analysis_watcher(app))


def main() -> None:
    global ALLOWED_USER_ID
    token, ALLOWED_USER_ID = _load_env()

    app = ApplicationBuilder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset))

    # Диалог ТЗ на монтаж — отдельная ветка, ловится ДО общего обработчика вопросов.
    montage_conv = ConversationHandler(
        entry_points=[
            CommandHandler("montage", montage_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_MONTAGE)}$"), montage_start),
        ],
        states={
            M_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, montage_source)],
            M_WINDOWS: [MessageHandler(filters.TEXT & ~filters.COMMAND, montage_windows)],
            M_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, montage_notes)],
        },
        fallbacks=[
            CommandHandler("cancel", montage_cancel),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_RESET)}$"), montage_cancel),
        ],
    )
    app.add_handler(montage_conv)

    # Диалог сравнения конкурентов — тоже отдельная ветка, ловится ДО общего обработчика.
    compete_conv = ConversationHandler(
        entry_points=[
            CommandHandler("compete", compete_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_COMPETE)}$"), compete_start),
        ],
        states={
            C_TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, compete_targets)],
            C_FOCUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, compete_focus)],
        },
        fallbacks=[
            CommandHandler("cancel", compete_cancel),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_RESET)}$"), compete_cancel),
        ],
    )
    app.add_handler(compete_conv)

    # Кнопки меню шлют свой текст-метку — ловим ДО общего обработчика вопросов.
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_STATUS)}$"), status))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_RESET)}$"), reset))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_HELP)}$"), start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ask))

    log.info("Пульт оркестра запущен. Владелец: %s", ALLOWED_USER_ID)
    app.run_polling()


if __name__ == "__main__":
    main()
