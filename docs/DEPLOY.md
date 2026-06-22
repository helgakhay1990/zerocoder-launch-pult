# Развёртывание бота на сервере (24/7)

Цель: бот живёт на VPS, переживает перезагрузку, авто-перезапускается. Управление — через `systemd`.

> ⚠️ Ключевое: мозг бота — `claude -p`. На сервере стоит Claude Code, а **авторизация — через `ANTHROPIC_API_KEY` в `.env` бота** (проверено: `claude -p` берёт ключ из окружения и не откатывается на подписку). Интерактивный `claude login` на сервере НЕ нужен. Все запросы всех пользователей идут через этот один ключ — ставь сюда ключ компании (платит компания), а не личную подписку.

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

## 2. Claude Code + авторизация по ключу

```bash
npm install -g @anthropic-ai/claude-code              # или официальный установщик
# Логиниться НЕ нужно. Проверяем работу через API-ключ:
ANTHROPIC_API_KEY=sk-ant-... claude -p "проверка: ответь одним словом ok"
```

Авторизация — **через `ANTHROPIC_API_KEY` в `.env` бота** (шаг 3): loader бота кладёт его в окружение, а подпроцесс `claude -p` наследует. Так не нужен интерактивный `claude login` (на сервере без экрана это морока), и расход идёт на ключ компании, а не на чью-то подписку. Конфиг/кэш claude всё равно лягут в `~/.claude` пользователя `pult` — поэтому в юните оставляем `HOME=/home/pult`.

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
nano .env          # TELEGRAM_BOT_TOKEN + ALLOWED_USER_ID + ANTHROPIC_API_KEY (ключ компании)
                   # OPEN_ACCESS=1 (по умолчанию) — бот открыт всем по ссылке;
                   # 0 — запереть на владельца + GUEST_USER_IDS

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

## Доступ коллегам

По умолчанию (`OPEN_ACCESS=1`) бот **открыт всем по ссылке** — даёшь ссылку, коллега заходит и пользуется, без списков ID. Все запросы идут через общий `ANTHROPIC_API_KEY`, а не подписку владельца. Чтобы запереть на круг лиц — `OPEN_ACCESS=0` + `GUEST_USER_IDS` в `.env`.
**Помни:** открытый бот + общий ключ = кто угодно по ссылке тратит ключ. Для контроля — лимит расхода на ключ в console.anthropic.com (Limits) и/или закрытый доступ.

## Чего стоит не забыть

- **Расход ключа.** Бот = один ключ на всех. Следи в console.anthropic.com (Usage/Cost), поставь лимит расхода (Limits). При исчерпании баланса `claude -p` падает с ошибкой ключа — на подписку НЕ перекидывается.
- **Монтаж тяжёлый.** whisper по часовой записи на слабом VPS — долго; либо мощный сервер (8+ ГБ), либо кнопку монтажа на сервере не включать (держать монтаж на локальной машине).
- **Секреты не в git.** `.env` (с `ANTHROPIC_API_KEY`), `.mcp.json`, `knowledge-base/` переносятся вручную и остаются на сервере.
- **Бэкап.** Периодически снимать `.env`/ключи — чтобы не настраивать заново.
