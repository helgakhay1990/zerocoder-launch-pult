"""Разовая авторизация Google Drive «вход как ты» — запускается ОДИН раз на машине с браузером.

Что делает: открывает браузер, ты жмёшь «Разрешить», получается token.json
(с refresh_token) — постоянный пропуск, который бот потом использует на сервере
без браузера.

Запуск (на Mac, из папки launch-pult):
    .venv/bin/pip install google-auth-oauthlib   # один раз
    .venv/bin/python scripts/gdrive_authorize.py secrets/client_secret.json

На выходе — secrets/token.json. Его кладём на сервер, путь в GOOGLE_OAUTH_TOKEN_FILE.
"""
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python scripts/gdrive_authorize.py <client_secret.json> "
              "[куда_сохранить_token.json]")
        sys.exit(1)
    client_file = Path(sys.argv[1]).expanduser()
    if not client_file.is_file():
        print(f"❌ Не найден файл клиента: {client_file}")
        sys.exit(1)
    out = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 \
        else client_file.parent / "token.json"

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("❌ Нет библиотеки. Поставь: .venv/bin/pip install google-auth-oauthlib")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    print("🌐 Открываю браузер — нажми «Разрешить»...")
    creds = flow.run_local_server(port=0)
    out.write_text(creds.to_json(), encoding="utf-8")
    print(f"✅ Готово. Пропуск сохранён: {out}")
    print("   Дальше скажи ассистенту «авторизовалась» — он перенесёт его на сервер.")


if __name__ == "__main__":
    main()
