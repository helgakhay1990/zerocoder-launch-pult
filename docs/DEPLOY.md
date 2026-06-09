# Развёртывание бота на сервере (24/7)

Цель: бот живёт на VPS, переживает перезагрузку, авто-перезапускается. Управление — через `systemd`.

> ⚠️ Ключевое: мозг бота — `claude -p`. **На сервере должен стоять Claude Code, залогиненный под аккаунтом, чьи лимиты мы готовы тратить.** Все запросы всех пользователей идут через этот один аккаунт.

---

## 0. Какой сервер

- **Облако:** РФ-провайдер (Timeweb / Selectel / Yandex Cloud) — меньше проблем с доступом к Telegram и оплатой.
- **ОС:** Ubuntu 22.04/24.04 LTS.
- **Размер:** для бота+анализа хватит 2 vCPU / 4 ГБ. **Для кнопки «ТЗ на монтаж» (whisper) нужно 8+ ГБ** и место под модели/видео — если монтаж на сервере не нужен, бери меньше и отключи эту кнопку.

---

## 1. Базовые пакеты

```bash
sudo adduser pult                      # отдельный пользователь для бота
sudo usermod -aG sudo pult
su - pult

sudo apt update && sudo apt install -y git python3 python3-venv python3-pip curl
# Node — нужен для Claude Code и playwright (npx @playwright/mcp)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Для кнопок анализа (playwright на headless-Linux нужны системные библиотеки):
npx playwright install --with-deps chromium

# Только если нужен монтаж на сервере (тяжело):
# sudo apt install -y ffmpeg tesseract-ocr tesseract-ocr-rus
# whisper-cpp — собрать отдельно либо отключить кнопку монтажа.
```

## 2. Claude Code + авторизация

```bash
npm install -g @anthropic-ai/claude-code     # или официальный установщик
claude login                                  # залогиниться под рабочим аккаунтом
claude -p "проверка: ответь одним словом ok"  # убедиться, что работает
```

Авторизация ляжет в `~/.claude` пользователя `pult` — именно его указываем в systemd-юните (`User=pult`, `HOME=/home/pult`).

## 3. Код + секреты + база знаний

```bash
cd ~
gh repo clone helgakhay1990/zerocoder-agent Ai-homework      # оркестр (публичный)
gh repo clone helgakhay1990/zerocoder-launch-pult Ai-homework/launch-pult

# venv бота
cd ~/Ai-homework/launch-pult
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# секрет бота
cp .env.example .env
nano .env          # TELEGRAM_BOT_TOKEN + ALLOWED_USER_ID

# MCP-ключи оркестра (Kinescope и пр.) — НЕ в git, перенести вручную:
nano ~/Ai-homework/agent-assistant/.mcp.json     # из .mcp.json.example + свои ключи
```

**Внутренняя база знаний** (`knowledge-base/`, с ценами/контактами) намеренно вне git — перенеси её на сервер отдельно (например, `scp` из локальной копии или из `_backups/knowledge-base-local/`):

```bash
# с локальной машины:
scp -r ~/Desktop/Ai-homework/knowledge-base pult@СЕРВЕР:~/Ai-homework/
```

## 4. systemd-сервис

```bash
sudo mkdir -p /var/log/pult-bot && sudo chown pult:pult /var/log/pult-bot
sudo cp ~/Ai-homework/launch-pult/deploy/pult-bot.service /etc/systemd/system/
# проверить пути/User в юните под свой логин, если не pult
sudo systemctl daemon-reload
sudo systemctl enable --now pult-bot      # запустить + автозапуск при перезагрузке
sudo systemctl status pult-bot            # должен быть active (running)
```

Управление:
```bash
sudo systemctl restart pult-bot           # после обновления кода
sudo systemctl stop pult-bot
journalctl -u pult-bot -f                 # живой лог
```

## 5. Обновление бота

```bash
cd ~/Ai-homework/launch-pult && git pull
cd ~/Ai-homework && git pull              # если менялся оркестр/скиллы
sudo systemctl restart pult-bot
```

---

## Доступ коллегам (когда понадобится)

Сейчас бот заперт на один ID (`ALLOWED_USER_ID`). Код подготовлен под расширение: добавить
ID коллег и роли — это правка `.env`, не переписывание (см. `.env.example`, секция гостей).
**Помни:** каждый запрос коллеги тратит твой лимит Claude и работает с твоими файлами —
поэтому по умолчанию для гостей предполагается ограниченный доступ (анализ/статус), а не полный.

## Чего стоит не забыть

- **Лимиты Claude.** Бот = один аккаунт на всех. Следи за расходом, ограничивай круг.
- **Монтаж тяжёлый.** whisper по часовой записи на слабом VPS — долго; либо мощный сервер, либо кнопку монтажа на сервере не включать.
- **Секреты не в git.** `.env`, `.mcp.json`, `knowledge-base/` переносятся вручную и остаются на сервере.
- **Бэкап.** Периодически снимать `~/.claude` (авторизация) и `.env`/ключи — чтобы не настраивать заново.
