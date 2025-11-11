# IMOEX Notifications Bot

Телеграм-бот, который отслеживает индекс Московской биржи IMOEX2 (все сессии) через ISS API и предоставляет оперативную информацию в личном диалоге с каждым пользователем:

- Каждую секунду обновляет служебное сообщение с текущим значением индекса и временем последнего обновления.
- Каждые 5 минут обновляет изображение с графиком изменения индекса за последние 5 часов. Нижняя граница графика начинается с минимального значения диапазона, чтобы сделать визуализацию читаемой.
- Отправляет дополнительные уведомления при резких изменениях (±15 пунктов за минуту) и автоматически удаляет их через час.

Для хранения информации о закреплённых сообщениях и накопленных значениях используется файл состояния `bot_state.json`, поэтому бот корректно восстанавливает работу после перезапуска.

## Подготовка окружения

1. Установите Python 3.11+.
2. Создайте и активируйте виртуальное окружение (рекомендуется):

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. Установите зависимости:

   ```bash
   pip install -r requirements.txt
   ```

4. Задайте переменные окружения (достаточно добавить их в `~/.bashrc`, `.env` или экспортировать перед запуском):

   - `TELEGRAM_BOT_TOKEN` — токен бота, полученный у [BotFather](https://t.me/BotFather).
   - `BOT_STATE_PATH` *(опционально)* — путь к файлу состояния. По умолчанию `bot_state.json` в корне проекта.

## Запуск локально

```bash
python bot.py
```

После запуска пользователю достаточно отправить боту команду `/start`. Бот создаст два служебных сообщения — с текущим значением индекса и графиком — и будет регулярно их редактировать вместо отправки новых, чтобы избежать спама.

## Развёртывание на VPS

Пример инструкции для Ubuntu 22.04/24.04:

1. **Создайте отдельного пользователя и подготовьте директорию проекта**

   ```bash
   sudo adduser --disabled-password --gecos "" imoexbot
   sudo usermod -aG sudo imoexbot
   sudo -iu imoexbot
   mkdir -p ~/apps
   cd ~/apps
   git clone https://github.com/Jagellonka/IMOEX_notifications.git imoex_notifications
   cd imoex_notifications
   ```

2. **Установите зависимости**

   ```bash
   sudo apt update && sudo apt install -y python3-venv python3-pip
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Создайте файл окружения** `~/apps/imoex_notifications/.env`:

   ```bash
   TELEGRAM_BOT_TOKEN=ваш_токен
   BOT_STATE_PATH=/home/imoexbot/apps/imoex_notifications/bot_state.json
   ```

4. **Настройте systemd-сервис** `/etc/systemd/system/imoex-bot.service`:

   ```ini
   [Unit]
   Description=IMOEX Telegram Bot
   After=network.target

   [Service]
   Type=simple
   User=imoexbot
   WorkingDirectory=/home/imoexbot/apps/imoex_notifications
   EnvironmentFile=/home/imoexbot/apps/imoex_notifications/.env
   ExecStart=/home/imoexbot/apps/imoex_notifications/.venv/bin/python bot.py
   Restart=on-failure
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

   После сохранения перезагрузите конфигурацию systemd:

   ```bash
   sudo systemctl daemon-reload
   ```

5. **Запустите сервис и добавьте в автозапуск**

   ```bash
   sudo systemctl enable --now imoex-bot.service
   ```

6. **Проверка статуса и журналов**

   ```bash
   sudo systemctl status imoex-bot.service
   journalctl -u imoex-bot.service -f
   ```

7. **Обновление бота**

   ```bash
   cd ~/apps/imoex_notifications
   git pull
   source .venv/bin/activate
   pip install -r requirements.txt
   sudo systemctl restart imoex-bot.service
   ```

После запуска бот автоматически поддерживает в каждом подключившемся чате два служебных сообщения, обновляет график каждые 5 минут, отслеживает резкие изменения индекса и удаляет оповещения через час после отправки.
