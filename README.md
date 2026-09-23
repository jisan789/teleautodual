# Telegram Broadcast Loop (Render Web Service Ready)

Automated Telegram group message broadcast runner using Telethon, engineered to run **24/7 continuously** on Render as a Web Service.

---

## Deploying to Render (24/7 Web Service)

### Option A: 1-Click / Blueprint Deploy
Push this repo to GitHub and link it to Render. Render will automatically detect [`render.yaml`](file:///c:/Users/Jisan/Desktop/loop/render.yaml).

### Option B: Manual Service Creation
1. Go to [Render Dashboard](https://dashboard.render.com/) -> **New** -> **Web Service**.
2. Connect your Git repository.
3. Configure the following settings:
   - **Name**: `telegram-broadcast-service` (or any name)
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python app.py`
4. *(Optional)* Add Environment Variables under **Environment**:
   - `CONFIG_URL`: `https://cdn.jisanfx.top/teleauto/config.json`
   - `TELEGRAM_API_ID`: *(Optional override)*
   - `TELEGRAM_API_HASH`: *(Optional override)*
   - `TELEGRAM_SESSION`: *(Optional override)*
   - `PYTHONUNBUFFERED`: `1`

---

## Free Tier vs 24/7 Keeping Awake

Render Free Web Services automatically enter sleep mode after 15 minutes of HTTP inactivity.

### Built-in Self Ping
[`app.py`](file:///c:/Users/Jisan/Desktop/loop/app.py) includes a built-in keep-alive worker that reads `RENDER_EXTERNAL_URL` (injected automatically by Render) and pings itself every 10 minutes to stay awake.

### External Monitor (Recommended for Free Tier)
To ensure 100% continuous uptime on free tier:
1. Copy your public Render Web Service URL (e.g., `https://your-service.onrender.com`).
2. Add a free 5-minute HTTP monitor at [cron-job.org](https://cron-job.org) or [UptimeRobot](https://uptimerobot.com) targeting `https://your-service.onrender.com/healthz`.

*(On Render's Starter plan ($7/mo), services never sleep and external monitors are not required).*

---

## Live Monitoring Dashboard & Health Endpoints

When running on Render, opening your service URL in a web browser displays a real-time monitor:
- **`GET /`**: Live visual dashboard showing bot status, connected Telegram account, message count, and target groups.
- **`GET /healthz`**: Returns HTTP 200 `{"status": "healthy", ...}` for Render health checks.
- **`GET /json`**: Raw telemetry data in JSON.

---

## Running Locally

```bash
# Run web service mode (matches Render production)
python app.py

# Or run standalone CLI mode
python show_writable_groups.py
```
