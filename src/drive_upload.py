"""Загрузка готового черновика (.docx) в Google Drive как живой Google Doc.

Зачем: бот уже отдаёт черновики .docx в Telegram. Этот модуль кладёт их ещё и
на Google Диск Ольги, конвертя в редактируемый Google Doc, и возвращает ссылку —
чтобы не скачивать-заливать руками.

Архитектура доступа (service account):
- В Google Cloud заведён service account, его JSON-ключ лежит локально.
- Ольга создаёт папку на своём Диске и расшаривает её на e-mail сервис-аккаунта
  (Editor). Бот заливает файлы ВНУТРЬ этой папки (parents=[folder_id]) — они
  видны в её Диске, ссылка открывается.
- Scope `drive.file` — минимальный: доступ только к файлам, что создал сам бот.

Безопасное поведение: если креды/папка не настроены или библиотеки не стоят —
функция возвращает None, бот продолжает работать как раньше (просто без ссылки).

Конфиг через окружение (.env бота):
  GOOGLE_SA_KEY_FILE  — путь к JSON-ключу сервис-аккаунта
  GOOGLE_DRIVE_FOLDER_ID — id папки на Диске, расшаренной на сервис-аккаунт
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("pult-bot.drive")

# MIME-типы: исходник .docx → конвертация в нативный Google Doc.
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_GDOC_MIME = "application/vnd.google-apps.document"
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def is_configured() -> bool:
    """Настроена ли выгрузка: есть путь к ключу (и файл существует) и id папки."""
    key = os.environ.get("GOOGLE_SA_KEY_FILE", "").strip()
    folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    return bool(key) and bool(folder) and Path(key).expanduser().is_file()


def upload_as_gdoc(docx_path: Path, title: str | None = None) -> str | None:
    """Залить .docx на Диск как Google Doc. Вернуть webViewLink или None.

    None означает «не настроено / не получилось» — вызывающий просто не покажет
    ссылку, отправка .docx в Telegram при этом не страдает.
    """
    if not is_configured():
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        log.warning("Google-библиотеки не установлены — выгрузка в Drive пропущена "
                    "(pip install google-api-python-client google-auth).")
        return None

    key_file = Path(os.environ["GOOGLE_SA_KEY_FILE"]).expanduser()
    folder_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"].strip()
    try:
        creds = service_account.Credentials.from_service_account_file(
            str(key_file), scopes=_SCOPES)
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        meta = {
            "name": title or docx_path.stem,
            "mimeType": _GDOC_MIME,       # просим Drive сконвертировать в Google Doc
            "parents": [folder_id],
        }
        media = MediaFileUpload(str(docx_path), mimetype=_DOCX_MIME, resumable=False)
        created = service.files().create(
            body=meta, media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        ).execute()
        link = created.get("webViewLink")
        log.info("Черновик выгружен в Drive: %s", link)
        return link
    except Exception:
        log.exception("Выгрузка в Google Drive не удалась (%s)", docx_path.name)
        return None
