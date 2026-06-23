# Авто-выгрузка черновиков на Google Диск — настройка

Бот присылает черновики `.docx` в Telegram. Эта настройка добавляет к ним
**ссылку на живой Google-документ** на Диске владельца — чтобы не скачивать-заливать руками.

Делается один раз. Пока не настроено — бот работает как есть, просто без ссылки.

## Почему «вход как владелец», а не сервис-аккаунт

Сервис-аккаунт («робот») НЕ работает на личном Gmail: у него нет своего места на
Диске, `files.create` падает с `storageQuotaExceeded`. Проверено боем 2026-06-23.
Рабочий способ — **пользовательский OAuth**: бот действует от имени владельца, файлы
лежат на его Диске и считаются его квотой. Scope минимальный — `drive.file` (доступ
только к файлам, что создал сам бот).

---

## Шаг 1. Google Cloud: проект + Drive API

Проект с включённым **Google Drive API** (один раз). Если уже есть — пропусти.
- <https://console.cloud.google.com/> → создать проект.
- Включить Drive API: <https://console.cloud.google.com/apis/library/drive.googleapis.com> → **Enable**.

## Шаг 2. Экран согласия (OAuth consent)

<https://console.cloud.google.com/apis/credentials/consent> → **Get started**:
- Имя приложения + почта владельца в полях поддержки/контакта.
- Audience — **External**.
- Дойти до конца → **Publish app** (статус «In production»). Это важно: иначе
  доступ протухает через 7 дней. Verification Google не требует — scope несенситивный.

## Шаг 3. OAuth-клиент (Desktop app)

<https://console.cloud.google.com/apis/credentials> → **Create Credentials → OAuth client ID**:
- Application type — **Desktop app** → **Create**.
- **Download JSON** → положить в `launch-pult/secrets/client_secret.json`.

## Шаг 4. Разовая авторизация (на машине с браузером)

```
cd launch-pult
.venv/bin/pip install google-auth-oauthlib        # один раз
.venv/bin/python scripts/gdrive_authorize.py secrets/client_secret.json secrets/token.json
```
Откроется браузер → выбрать аккаунт → **Разрешить** (если «приложение не проверено» —
«Дополнительно → перейти», это своё же приложение). На выходе — `secrets/token.json`
с `refresh_token` (постоянный пропуск, браузер больше не нужен).

## Шаг 5. Положить пропуск на сервер и включить

```
scp secrets/token.json root@СЕРВЕР:/opt/pult-bot/launch-pult/secrets/token.json
```
В `.env` бота (на сервере) добавить:
```
GOOGLE_OAUTH_TOKEN_FILE=/opt/pult-bot/launch-pult/secrets/token.json
GOOGLE_DRIVE_FOLDER_ID=<id папки на Диске>   # пусто = «Мой диск»
```
Перезапустить бота (`pm2 restart pult-bot`).

> `secrets/`, `token.json`, `client_secret.json` закрыты от git — наружу не уйдут.

---

## Как проверить

Запусти любую задачу бота. Когда придёт `.docx`, в подписи под файлом появится:
```
📄 Открыть в Google Docs: https://docs.google.com/document/d/...
```
Ссылка ведёт на редактируемый Google-документ на Диске владельца.
Нет строки — бот не нашёл `token.json` или google-библиотеки; `.docx` при этом приходит как обычно.
