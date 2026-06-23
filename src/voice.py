"""Расшифровка голосовых сообщений Telegram в текст (ogg/opus → текст).

Telegram шлёт голосовые в OGG/Opus. Тут: ffmpeg переводит в wav 16кГц моно,
whisper-cli (whisper.cpp, уже стоит для монтажа) распознаёт речь. Модель `base`
— общая с монтажом (та же папка), при отсутствии скачивается один раз.

Расшифровка не идеальна на именах/латинице, но для коротких ответов на вопросы
бота («сравни по цене, формату и гарантии») — более чем достаточно.
"""
from __future__ import annotations

import logging
import os
import subprocess
import urllib.request
from pathlib import Path

log = logging.getLogger("pult-bot.voice")

PROJECT_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path(__file__).resolve().parents[2]))
MODELS_DIR = PROJECT_ROOT / "agent-assistant" / "models"
MODEL = os.environ.get("VOICE_WHISPER_MODEL", "base")
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{m}.bin"
LANG = os.environ.get("VOICE_LANG", "ru")


def _ensure_model() -> Path | None:
    path = MODELS_DIR / f"ggml-{MODEL}.bin"
    if path.exists():
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    url = MODEL_URL.format(m=MODEL)
    log.info("Скачиваю модель whisper '%s' для голосовых (один раз)...", MODEL)
    try:
        urllib.request.urlretrieve(url, path)
        return path
    except Exception:
        log.exception("Не удалось скачать модель whisper '%s'", MODEL)
        if path.exists():
            path.unlink(missing_ok=True)
        return None


def transcribe(ogg_path: Path) -> str | None:
    """Голосовое (ogg) → распознанный текст. None при сбое (тогда бот попросит текстом)."""
    if not (shutil_which("ffmpeg") and shutil_which("whisper-cli")):
        log.warning("Нет ffmpeg/whisper-cli — голосовые не расшифровать.")
        return None
    model = _ensure_model()
    if not model:
        return None

    wav = ogg_path.with_suffix(".wav")
    prefix = ogg_path.with_suffix("")     # whisper -otxt пишет prefix.txt
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(ogg_path), "-ar", "16000", "-ac", "1", str(wav)],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["whisper-cli", "-m", str(model), "-f", str(wav), "-l", LANG,
             "-nt", "-otxt", "-of", str(prefix)],
            capture_output=True, check=True, timeout=180,
        )
        txt_file = Path(f"{prefix}.txt")
        if txt_file.exists():
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
            return text or None
        return None
    except Exception:
        log.exception("Расшифровка голосового не удалась")
        return None
    finally:
        for f in (wav, Path(f"{prefix}.txt")):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)
