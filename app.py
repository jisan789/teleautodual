"""
Render Web Service Entrypoint for Telegram Broadcast Loop
-----------------------------------------------------------
- Runs the Telegram broadcast worker in a resilient background thread.
- Binds to Render's dynamic PORT (0.0.0.0:$PORT) to pass HTTP health checks.
- Provides a live web dashboard at `/` and health checks at `/healthz`.
- Includes an automatic keep-alive ping loop for Render free tier.
"""

import os
import sys
import time
import json
import signal
import asyncio
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

# Import worker logic and live status tracking from show_writable_groups
from show_writable_groups import main as run_telegram_worker, STATUS, safe, format_duration


# ─────────────────────────────────────────────────────────────────────
# 1. Resilient Background Worker Thread
# ─────────────────────────────────────────────────────────────────────

def background_worker_loop():
    """Supervises the Telegram worker, ensuring continuous 24/7 execution."""
    # Ensure this secondary thread has its own active asyncio event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("[WORKER] Background Telegram broadcast thread initialized.", flush=True)

    while True:
        try:
            print("[WORKER] Launching Telegram broadcast task...", flush=True)
            STATUS["state"] = "starting"
            STATUS["last_error"] = None
            run_telegram_worker(loop_mode=True)
        except Exception as e:
            import traceback
            err_msg = str(e)
            STATUS["state"] = "error"
            STATUS["last_error"] = err_msg
            print(f"[WORKER ERROR] Broadcast task encountered an exception:\n{traceback.format_exc()}", flush=True)
            print("[WORKER] Auto-recovering: restarting worker in 30 seconds...", flush=True)
            time.sleep(30)


# ─────────────────────────────────────────────────────────────────────
# 2. Self Keep-Alive Loop (Prevents Render Free Tier from Sleeping)
# ─────────────────────────────────────────────────────────────────────

def keep_alive_loop():
    """
    Render Free Web Services spin down after 15 minutes of inactivity.
    This thread periodically pings the public URL to keep it awake.
    """
    service_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("KEEP_ALIVE_URL")
    if not service_url:
        print("[KEEP-ALIVE] RENDER_EXTERNAL_URL not set. Running in standard server mode.", flush=True)
        return

    # Ensure URL ends without trailing slash collision
    ping_url = service_url.rstrip("/") + "/healthz"
    print(f"[KEEP-ALIVE] Self-ping active. Target: {ping_url} (interval: 10m)", flush=True)

    # Initial delay before starting pings
    time.sleep(120)

    while True:
        try:
            req = urllib.request.Request(
                ping_url,
                headers={"User-Agent": "RenderKeepAlive/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    print(f"[KEEP-ALIVE] Health check ping successful at {time.strftime('%X')}", flush=True)
        except Exception as e:
            print(f"[KEEP-ALIVE] Ping notification: {e}", flush=True)

        time.sleep(600)  # Ping every 10 minutes


# ─────────────────────────────────────────────────────────────────────
# 3. HTTP Server & Health Check Dashboard
# ─────────────────────────────────────────────────────────────────────

def get_dashboard_html():
    uptime_sec = time.time() - STATUS.get("start_time", time.time())
    uptime_str = format_duration(uptime_sec)
    
    state = STATUS.get("state", "unknown").upper()
    badge_color = "#10b981" if state in ("BROADCASTING", "RUNNING") else ("#f59e0b" if state in ("STARTING", "CONNECTING", "FETCHING_CONFIG") else "#ef4444")
    
    account = STATUS.get("account") or "Connecting..."
    total_sent = STATUS.get("total_sent", 0)
    writable = STATUS.get("writable_groups", 0)
    target = STATUS.get("target_groups", 0)
    last_sent = STATUS.get("last_sent_time") or "None yet"
    last_error = STATUS.get("last_error") or "None"
    config_url = STATUS.get("config_url", "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="30">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Telegram Broadcast Bot - 24/7 Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: rgba(22, 29, 47, 0.7);
      --card-border: rgba(255, 255, 255, 0.08);
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.15);
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
      background: radial-gradient(circle at 50% 0%, #1e293b, var(--bg) 75%);
      color: var(--text-main);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .container {{
      max-width: 680px;
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      backdrop-filter: blur(16px);
      border-radius: 20px;
      padding: 32px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.45);
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
    }}
    .title-box h1 {{
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .title-box p {{
      color: var(--text-muted);
      font-size: 0.85rem;
      margin-top: 4px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.05em;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--card-border);
    }}
    .dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: {badge_color};
      box-shadow: 0 0 10px {badge_color};
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{
      0% {{ transform: scale(0.95); opacity: 0.8; }}
      50% {{ transform: scale(1.2); opacity: 1; }}
      100% {{ transform: scale(0.95); opacity: 0.8; }}
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    @media (max-width: 540px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
    .card {{
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid var(--card-border);
      padding: 16px 20px;
      border-radius: 14px;
    }}
    .card-label {{
      font-size: 0.75rem;
      color: var(--text-muted);
      text-transform: uppercase;
      font-weight: 600;
      letter-spacing: 0.05em;
    }}
    .card-value {{
      font-size: 1.25rem;
      font-weight: 700;
      margin-top: 6px;
      color: var(--text-main);
    }}
    .info-panel {{
      background: rgba(15, 23, 42, 0.4);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 16px;
      font-size: 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .info-row {{
      display: flex;
      justify-content: space-between;
      word-break: break-all;
    }}
    .info-key {{
      color: var(--text-muted);
      min-width: 120px;
    }}
    .info-val {{
      font-weight: 500;
      text-align: right;
    }}
    .footer {{
      margin-top: 20px;
      text-align: center;
      font-size: 0.75rem;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="title-box">
        <h1>✈️ Telegram Broadcast Bot</h1>
        <p>Continuous Render Web Service · 24/7 Worker</p>
      </div>
      <div class="badge">
        <span class="dot"></span>
        <span style="color: {badge_color}">{state}</span>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="card-label">Messages Dispatched</div>
        <div class="card-value" style="color: #38bdf8;">{total_sent}</div>
      </div>
      <div class="card">
        <div class="card-label">Target Groups</div>
        <div class="card-value">{target} <span style="font-size: 0.85rem; color: var(--text-muted); font-weight: normal;">(of {writable} writable)</span></div>
      </div>
      <div class="card">
        <div class="card-label">Logged In User</div>
        <div class="card-value" style="font-size: 1rem; font-weight: 600;">{account}</div>
      </div>
      <div class="card">
        <div class="card-label">Service Uptime</div>
        <div class="card-value" style="font-size: 1rem; font-weight: 600;">{uptime_str}</div>
      </div>
    </div>

    <div class="info-panel">
      <div class="info-row">
        <span class="info-key">Last Broadcast:</span>
        <span class="info-val">{last_sent}</span>
      </div>
      <div class="info-row">
        <span class="info-key">Config Source:</span>
        <span class="info-val"><a href="{config_url}" target="_blank" style="color: #38bdf8; text-decoration: none;">{config_url}</a></span>
      </div>
      <div class="info-row">
        <span class="info-key">Last Status:</span>
        <span class="info-val" style="color: {'#ef4444' if last_error != 'None' else '#10b981'};">{last_error}</span>
      </div>
    </div>

    <div class="footer">
      Auto-refreshes every 30s · Health check: <code>/healthz</code> · JSON API: <code>/json</code>
    </div>
  </div>
</body>
</html>
"""


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Handles Render HTTP health checks and dashboard requests."""

    def do_GET(self):
        # Strip query parameters if any
        path = self.path.split("?")[0]

        if path in ("/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = json.dumps({
                "status": "healthy",
                "state": STATUS.get("state", "unknown"),
                "total_sent": STATUS.get("total_sent", 0),
                "uptime_seconds": int(time.time() - STATUS.get("start_time", time.time()))
            })
            self.wfile.write(payload.encode("utf-8"))

        elif path == "/json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(STATUS, default=str, indent=2).encode("utf-8"))

        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = get_dashboard_html()
            self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress routine GET logs to prevent cluttering Render console
        return


# ─────────────────────────────────────────────────────────────────────
# 4. Main Service Execution
# ─────────────────────────────────────────────────────────────────────

def main():
    # 1. Start the Telegram broadcast worker in a background daemon thread
    worker_thread = threading.Thread(target=background_worker_loop, daemon=True, name="TelegramBroadcastWorker")
    worker_thread.start()

    # 2. Start the self-ping keep alive worker
    keep_alive_thread = threading.Thread(target=keep_alive_loop, daemon=True, name="KeepAliveWorker")
    keep_alive_thread.start()

    # 3. Bind HTTP Server to 0.0.0.0:$PORT required by Render
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"===========================================================", flush=True)
    print(f"[RENDER HTTP SERVER] Listening on 0.0.0.0:{port}", flush=True)
    print(f"[RENDER HTTP SERVER] Health Check: http://0.0.0.0:{port}/healthz", flush=True)
    print(f"===========================================================", flush=True)

    def handle_shutdown(signum, frame):
        print("\n[SYSTEM] Termination signal received. Shutting down gracefully...", flush=True)
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
