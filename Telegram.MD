# Telegram Bot Setup Guide

This guide walks through setting up a Telegram bot for your project (e.g., GhostFrog Agentic Alert Bot).

---

## 🧩 1. Create the Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Start a chat and send the command:
   ```
   /newbot
   ```
3. Follow the prompts to give your bot a **name** and a **username** (must end with `_bot`).
4. BotFather will respond with your **bot token**, something like:
   ```
   123456789:ABCDefGhIJKlmNoPQRstuVWxyz12345678
   ```
5. Save this token securely — you’ll need it for your `.env` file.

---

## ⚙️ 2. Add Environment Variables

In your project’s root `.env` file, add:

```bash
TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIJKlmNoPQRstuVWxyz12345678
TELEGRAM_CHAT_ID=123456789
```

- `TELEGRAM_BOT_TOKEN` — from BotFather.
- `TELEGRAM_CHAT_ID` — your chat or group ID.

To get your **chat ID**, send a message to your bot, then visit:
```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```
Look for `"chat":{"id":123456789}` in the JSON response.

---

## 🧠 3. Send a Test Message (Python)

Create a quick script called `send_test_message.py`:

```python
import os
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MESSAGE = "Test message from GhostFrog Agentic Alert Bot!"

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
payload = {"chat_id": CHAT_ID, "text": MESSAGE}

resp = requests.post(url, json=payload)
print(resp.json())
```

Run it with:
```bash
python3 send_test_message.py
```

If everything’s working, you’ll receive the message in Telegram.

---

## 🧩 4. Integrate into Your Project

Example usage inside your alert logic:

```python
from infrastructure.utils.alerts import send_telegram_message

send_telegram_message("🚨 New profitable listing found!")
```

Example helper (`infrastructure/utils/alerts.py`):

```python
import os
import requests

def send_telegram_message(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})
```

---

## ✅ Done

Your Telegram bot is ready! You can now automate alerts, heartbeat pings, ROI summaries, or any event notifications.
