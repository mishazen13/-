# Telegram bot для привязки аккаунта (LimboAuth / LimboAuthSocialAddon)

Этот проект поднимает:
- Telegram-бота (aiogram) для выдачи одноразового кода привязки.
- HTTP API (FastAPI), в которое ваш сервер/bridge из `LimboAuthSocialAddon` отправляет `nickname + code`.

После подтверждения связка сохраняется в SQLite.

## Что умеет
- `/start` — краткая инструкция.
- `/link` — выдать одноразовый код привязки.
- `/status` — показать, какой ник привязан.
- `POST /limboauthsocialaddon/confirm` — подтвердить код и привязать ник.
- `GET /limboauthsocialaddon/check/{nickname}` — проверить, есть ли привязка.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env`:
- `BOT_TOKEN` — токен Telegram-бота.
- `API_KEY` — секрет для защиты HTTP API.
- Остальное можно оставить по умолчанию.

## Запуск

```bash
python bot.py
```

Сервис запускает одновременно Telegram polling + HTTP сервер.

## Пример запроса из bridge/плагина

Если ваш `LimboAuthSocialAddon` умеет дергать внешний URL при вводе кода, отправляйте:

```bash
curl -X POST "http://127.0.0.1:8080/limboauthsocialaddon/confirm" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: replace_me_with_long_random_secret" \
  -d '{"nickname":"Player123","code":"123456","uuid":"optional-player-uuid"}'
```

Успех:

```json
{"ok":true,"telegram_id":123456789}
```

Проверка привязки:

```bash
curl "http://127.0.0.1:8080/limboauthsocialaddon/check/Player123" \
  -H "X-API-Key: replace_me_with_long_random_secret"
```

## Как подружить с LimboAuthSocialAddon

У разных сборок addon способы интеграции отличаются, но базовый принцип одинаковый:
1. Игрок получает в Telegram командой `/link` одноразовый код.
2. Игрок вводит этот код в Minecraft (через ваш social command/flow).
3. Плагин или промежуточный bridge отправляет `nickname + code` в `POST /limboauthsocialaddon/confirm`.
4. Бот уведомляет игрока об успешной привязке.

Если хотите, могу во втором шаге дать точный готовый конфиг именно под вашу версию `LimboAuthSocialAddon` (нужен пример текущего config.yml).
