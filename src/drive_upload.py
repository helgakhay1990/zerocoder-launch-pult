"""Загрузка готового черновика (.docx) в Google Drive как живой Google Doc.

Способ доступа — ПОЛЬЗОВАТЕЛЬСКИЙ OAuth («вход как Ольга»): файлы принадлежат
ей, лежат на её Диске, квота её → нет стены `storageQuotaExceeded`, которая
убивает путь через service account на личном Gmail (проверено 2026-06-23).

Как заводится (один раз):
1. В Google Cloud (проект с включённым Drive API) создаётся OAuth-клиент типа
   «Desktop app» → скачивается client_secret.json.
2. На машине с браузером запускается scripts/gdrive_authorize.py → Ольга жмёт
   «Разрешить» → получается token.json с refresh_token.
3. token.json кладётся на сервер, путь к нему — в GOOGLE_OAUTH_TOKEN_FILE.
   Дальше браузер не нужен: refresh_token сам обновляет доступ.

Безопасное поведение: нет token.json / нет библиотек → функция возвращает None,
бот продолжает работать как раньше (шлёт .docx в Telegram, просто без ссылки).

Конфиг через окружение (.env бота):
  GOOGLE_OAUTH_TOKEN_FILE — путь к token.json (refresh_token + client creds)
  GOOGLE_DRIVE_FOLDER_ID  — id папки на Диске (необязательно; пусто = «Мой диск»)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("pult-bot.drive")

# MIME-типы: исходник .docx → конвертация в нативный Google Doc.
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_GDOC_MIME = "application/vnd.google-apps.document"
# drive.file — минимальный scope: доступ только к файлам, что создал сам бот.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _token_file() -> Path | None:
    p = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip()
    return Path(p).expanduser() if p else None


def is_configured() -> bool:
    """Настроена ли выгрузка: задан путь к token.json и файл существует."""
    tf = _token_file()
    return bool(tf) and tf.is_file()


def _load_user_creds():
    """Загрузить пользовательские креды из token.json, обновить если протухли.
    Вернёт google credentials или бросит исключение."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    tf = _token_file()
    creds = Credentials.from_authorized_user_file(str(tf), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # сохранить обновлённый access_token обратно (refresh_token не меняется)
            tf.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError("token.json невалиден и не обновляется — пере-авторизуй.")
    return creds


def upload_as_gdoc(docx_path: Path, title: str | None = None) -> str | None:
    """Залить .docx на Диск как Google Doc. Вернуть webViewLink или None.

    None означает «не настроено / не получилось» — вызывающий просто не покажет
    ссылку, отправка .docx в Telegram при этом не страдает.
    """
    if not is_configured():
        return None
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        log.warning("Google-библиотеки не установлены — выгрузка в Drive пропущена "
                    "(pip install google-api-python-client google-auth).")
        return None

    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    try:
        creds = _load_user_creds()
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        meta = {
            "name": title or docx_path.stem,
            "mimeType": _GDOC_MIME,       # просим Drive сконвертировать в Google Doc
        }
        if folder_id:
            meta["parents"] = [folder_id]
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
