# Telegram Multi-Account Broadcast Loop (Render Web Service Ready)

Automated Telegram group message broadcast runner using Telethon, engineered to run **24/7 continuously** on Render as a Web Service.

Supports **3 main accounts (Account 1, Account 2, and Account 3)** running simultaneously in isolated threads with separate rate limits, slowmode timers, message tracking, and target channels.

**No configurations or credentials are hardcoded in your local code files.** All account credentials and broadcast options are loaded dynamically from your remote `CONFIG_URL` (or environment variables).

---

## Account Credentials (`show_writable_groups.py`)

Credentials for all 3 accounts are set inside [`show_writable_groups.py`](file:///c:/Users/Jisan/Desktop/loop/show_writable_groups.py) under `ACCOUNT_CREDENTIALS`:

```python
ACCOUNT_CREDENTIALS = {
    "account_1": {
        "name": "Account 1",
        "api_id": 35126153,
        "api_hash": "a95d613cee72019ccb984d8686c3e592",
        "session": "1BVtsOGwBuwLZqUsI-S3XhZKvg5ni0P_...",
    },
    "account_2": {
        "name": "Account 2",
        "api_id": 33591633,
        "api_hash": "a4e0dc8c681a8c6afe6124140b163768",
        "session": "1BVtsOGwBu3yGzPNv-fsFflxZZfVVJ5...",
    },
    "account_3": {
        "name": "Account 3",
        "api_id": YOUR_API_ID_3,
        "api_hash": "YOUR_API_HASH_3",
        "session": "YOUR_SESSION_KEY_3",
    },
}
```

---

## Remote Broadcast Campaign Configuration (`dualconfig.json`)

Host this JSON at your remote CDN URL (`CONFIG_URL`). It controls the broadcast campaigns without exposing your API credentials:

```json
{
  "account_1": {
    "clear_previous_messages": true,
    "clear_messages_scan_limit": 100,
    "message": [
      "https://t.me/+P06FEzU4xCkyYTc1"
    ],
    "message_limit_per_group": 2,
    "interval_minutes": "3-5",
    "rounds": 0,
    "target_groups": "all"
  },
  "account_2": {
    "clear_previous_messages": true,
    "clear_messages_scan_limit": 100,
    "message": [
      "https://t.me/+juCjMCIEZWUzYmJl"
    ],
    "message_limit_per_group": 2,
    "interval_minutes": "3-5",
    "rounds": 0,
    "target_groups": "all"
  },
  "account_3": {
    "clear_previous_messages": true,
    "clear_messages_scan_limit": 100,
    "message": [
      "https://t.me/+YourAccount3Link"
    ],
    "message_limit_per_group": 2,
    "interval_minutes": "3-5",
    "rounds": 0,
    "target_groups": "all"
  }
}
```

### Options Available Per Account:
- **`clear_previous_messages`**: `true` (default: `true`) to automatically scan and unsend/delete all previously sent messages from all groups on startup before starting the broadcast loop.
- **`clear_messages_scan_limit`**: Maximum recent messages to scan per group during cleanup (default: `100`).
- **`message`**: The broadcast text or link (supports an array of lines or a single string).
- **`interval_minutes`**: Interval range between sends (e.g. `"3-5"`).
- **`message_limit_per_group`**: Maximum messages to retain per group (e.g. `2` to keep last 2 and delete older ones).
- **`target_groups`**: `"all"` (broadcasts to all groups that account is in) or a list of specific group IDs/names (e.g. `[-1001234567, "Group Title"]`).
- **`rounds`**: `0` for infinite continuous loop.


---

## How 2 Accounts Work

1. **Simultaneous & Completely Isolated**:
   - Both accounts run in dedicated parallel threads at the exact same time.
   - If Account 1 encounters SlowMode or FloodWait in one of its groups, Account 2 is completely unaffected and continues posting to its channels immediately.
   - Each account has its own independent client connection, message deletion tracker, and target groups.

2. **Clean Separation of Concerns**:
   - Credentials (`api_id`, `api_hash`, `session`) live securely inside `show_writable_groups.py`.
   - Campaign settings (`message`, `interval_minutes`, `message_limit_per_group`, `target_groups`) are fetched and synced dynamically from `CONFIG_URL`.

---

## Generating Session Strings ("Season Keys")

To generate the session string ("season key") for Account 2:

```bash
python generate_session.py
```

Follow the prompts to enter your phone number and login code. Copy the generated string and paste it into `config.json` under `account_2` -> `"season"`.

---

## Deploying to Render (24/7 Web Service)

1. Go to [Render Dashboard](https://dashboard.render.com/) -> **New** -> **Web Service**.
2. Connect your Git repository.
3. Configure the following settings:
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python app.py`
4. Add Environment Variables under **Environment**:
   - `CONFIG_URL`: `https://cdn.jisanfx.top/teleauto/config.json`
   - `PYTHONUNBUFFERED`: `1`

---

## Live Monitoring Dashboard

Opening your service URL (or `http://localhost:8080`) displays a real-time monitor:
- **`GET /`**: Live visual dashboard showing status, messages sent, interval, deletion limit, message preview, and writable/target groups for **both Account 1 and Account 2**.
- **`GET /healthz`**: Returns HTTP 200 `{"status": "healthy", ...}` for Render health checks.
- **`GET /json`**: Full telemetry JSON data.

---

## Running Locally

```bash
# Run web service mode with live dashboard (matches Render production)
python app.py

# Or run standalone CLI mode
python show_writable_groups.py
```
