# Agent OS — Telegram Bridge

Long-polls the Telegram Bot API and forwards allowed messages to Agent OS.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) → get `TELEGRAM_BOT_TOKEN`
2. Set environment variables:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
AGENT_OS_URL=http://localhost:8000
AGENT_OS_SECRET=your-api-key
TELEGRAM_ALLOW_FROM=123456789,@yourusername   # leave empty for open mode
```

3. Run:

```bash
python main.py
# or
docker build -t agentos-telegram . && docker run --env-file .env agentos-telegram
```

## Docker Compose (add to root docker-compose.yml)

```yaml
  telegram-bridge:
    build: ./channels/telegram-bridge
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - AGENT_OS_URL=http://agent-os:8000
      - AGENT_OS_SECRET=${API_KEY_CHANNEL}
      - TELEGRAM_ALLOW_FROM=${TELEGRAM_ALLOW_FROM}
    depends_on:
      - agent-os
```
