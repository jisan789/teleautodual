"""
Render Web Service Entrypoint for Multi-Account Telegram Broadcast Loop
------------------------------------------------------------------------
- Runs the multi-account Telegram broadcast supervisor in a resilient background thread.
- Simultaneously executes isolated broadcast loops for Account 1 and Account 2.
- Binds to Render's dynamic PORT (0.0.0.0:$PORT) to pass HTTP health checks.
- Provides a live web dashboard at `/` displaying individual status for all accounts.
- Includes automatic keep-alive ping loop for Render free tier.
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
    """Supervises the Telegram multi-account supervisor, ensuring 24/7 continuous operation."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("[WORKER] Multi-Account Background Broadcast supervisor thread initialized.", flush=True)

    while True:
        try:
            print("[WORKER] Launching multi-account broadcast tasks...", flush=True)
            STATUS["state"] = "starting"
            STATUS["last_error"] = None
            run_telegram_worker(loop_mode=True)
        except Exception as e:
            import traceback
            err_msg = str(e)
            STATUS["state"] = "error"
            STATUS["last_error"] = err_msg
            print(f"[WORKER ERROR] Broadcast supervisor encountered an exception:\n{traceback.format_exc()}", flush=True)
            print("[WORKER] Auto-recovering: restarting supervisor in 30 seconds...", flush=True)
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
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        print(f"[KEEP-ALIVE] Health check ping successful at {time.strftime('%X')}", flush=True)
            except Exception:
                import ssl
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    if resp.status == 200:
                        print(f"[KEEP-ALIVE] Health check ping successful at {time.strftime('%X')}", flush=True)
        except Exception as e:
            print(f"[KEEP-ALIVE] Ping notification: {e}", flush=True)

        time.sleep(600)  # Ping every 10 minutes


# ─────────────────────────────────────────────────────────────────────
# 3. HTTP Server & Multi-Account Health Check Dashboard
# ─────────────────────────────────────────────────────────────────────

def get_badge_info(state):
    state = (state or "unknown").upper()
    if state in ("BROADCASTING", "RUNNING"):
        return "#10b981", state  # Green
    elif state in ("STARTING", "CONNECTING", "FETCHING_CONFIG"):
        return "#f59e0b", state  # Amber
    elif state in ("UNCONFIGURED", "IDLE"):
        return "#64748b", state  # Gray
    else:
        return "#ef4444", state  # Red


def render_account_card(acc_id, acc_data):
    name = acc_data.get("name") or acc_id
    state = acc_data.get("state", "unknown")
    badge_color, badge_text = get_badge_info(state)

    account_user = acc_data.get("account") or ("Not Configured" if state == "unconfigured" else "Connecting...")
    total_sent = acc_data.get("total_sent", 0)
    writable = acc_data.get("writable_groups", 0)
    target = acc_data.get("target_groups", 0)
    last_sent = acc_data.get("last_sent_time") or "None yet"
    last_error = acc_data.get("last_error") or "None"
    interval = acc_data.get("interval", "3-5")
    limit = acc_data.get("limit", 0)
    limit_str = f"Keep {limit}" if limit > 0 else "Unlimited"
    msg_preview = acc_data.get("message_preview") or "No message loaded"

    is_unconfigured = state == "unconfigured"

    return f"""
    <div class="account-card {'unconfigured-card' if is_unconfigured else ''}">
      <div class="account-card-header">
        <div class="account-title">
          <h3>👤 {name}</h3>
          <p>{account_user}</p>
        </div>
        <div class="badge">
          <span class="dot" style="background: {badge_color}; box-shadow: 0 0 10px {badge_color};"></span>
          <span style="color: {badge_color}; font-size: 0.72rem;">{badge_text}</span>
        </div>
      </div>

      <div class="account-stats-grid">
        <div class="stat-mini">
          <span class="stat-label">Dispatched</span>
          <span class="stat-num" style="color: #38bdf8;">{total_sent}</span>
        </div>
        <div class="stat-mini">
          <span class="stat-label">Targets / Writable</span>
          <span class="stat-num">{target} <span style="font-size: 0.75rem; color: var(--text-muted); font-weight: normal;">/ {writable}</span></span>
        </div>
        <div class="stat-mini">
          <span class="stat-label">Interval</span>
          <span class="stat-num" style="font-size: 0.95rem;">{interval}m</span>
        </div>
        <div class="stat-mini">
          <span class="stat-label">Deletion Limit</span>
          <span class="stat-num" style="font-size: 0.95rem;">{limit_str}</span>
        </div>
      </div>

      <div class="account-meta">
        <div class="meta-row">
          <span class="meta-key">Unique Text:</span>
          <span class="meta-val" style="color: #cbd5e1; font-style: italic; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">"{msg_preview}"</span>
        </div>
        <div class="meta-row">
          <span class="meta-key">Last Broadcast:</span>
          <span class="meta-val">{last_sent}</span>
        </div>
        <div class="meta-row">
          <span class="meta-key">Status / Error:</span>
          <span class="meta-val" style="color: {'#ef4444' if last_error not in ('None', None) and not is_unconfigured else ('#94a3b8' if is_unconfigured else '#10b981')};">{last_error}</span>
        </div>
      </div>
    </div>
    """


def get_dashboard_html():
    uptime_sec = time.time() - STATUS.get("start_time", time.time())
    uptime_str = format_duration(uptime_sec)

    global_state = STATUS.get("state", "unknown").upper()
    badge_color, badge_text = get_badge_info(global_state)

    total_sent = STATUS.get("total_sent", 0)
    config_url = STATUS.get("config_url", "")
    accounts_dict = STATUS.get("accounts", {})

    active_accounts_count = sum(1 for a in accounts_dict.values() if a.get("state") in ("broadcasting", "running", "connecting", "starting"))
    total_accounts_count = len(accounts_dict)

    account_cards_html = "".join([render_account_card(aid, adata) for aid, adata in accounts_dict.items()])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="30">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Telegram Multi-Account Broadcast Bot - Live Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: rgba(22, 29, 47, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-inner: rgba(15, 23, 42, 0.6);
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
      max-width: 820px;
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      backdrop-filter: blur(16px);
      border-radius: 24px;
      padding: 32px;
      box-shadow: 0 24px 50px rgba(0, 0, 0, 0.5);
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
      font-size: 1.4rem;
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
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{
      0% {{ transform: scale(0.95); opacity: 0.8; }}
      50% {{ transform: scale(1.2); opacity: 1; }}
      100% {{ transform: scale(0.95); opacity: 0.8; }}
    }}
    .global-stats {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 14px;
      margin-bottom: 24px;
    }}
    @media (max-width: 640px) {{
      .global-stats {{ grid-template-columns: 1fr; }}
    }}
    .global-stat-card {{
      background: var(--card-inner);
      border: 1px solid var(--card-border);
      padding: 16px 20px;
      border-radius: 14px;
    }}
    .stat-label {{
      font-size: 0.72rem;
      color: var(--text-muted);
      text-transform: uppercase;
      font-weight: 600;
      letter-spacing: 0.05em;
    }}
    .stat-val {{
      font-size: 1.35rem;
      font-weight: 700;
      margin-top: 6px;
      color: var(--text-main);
    }}
    .section-title {{
      font-size: 0.9rem;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 14px;
    }}
    .accounts-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    @media (max-width: 700px) {{
      .accounts-grid {{ grid-template-columns: 1fr; }}
    }}
    .account-card {{
      background: var(--card-inner);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }}
    .account-card:hover {{
      border-color: rgba(56, 189, 248, 0.3);
    }}
    .unconfigured-card {{
      opacity: 0.65;
      border-style: dashed;
    }}
    .account-card-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
    }}
    .account-title h3 {{
      font-size: 1.05rem;
      font-weight: 700;
    }}
    .account-title p {{
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-top: 2px;
    }}
    .account-stats-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
      background: rgba(0, 0, 0, 0.2);
      padding: 12px;
      border-radius: 10px;
      border: 1px solid rgba(255, 255, 255, 0.04);
    }}
    .stat-mini {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .stat-num {{
      font-size: 1.1rem;
      font-weight: 700;
    }}
    .account-meta {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 0.8rem;
    }}
    .meta-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      word-break: break-all;
    }}
    .meta-key {{
      color: var(--text-muted);
      min-width: 105px;
    }}
    .meta-val {{
      font-weight: 500;
      text-align: right;
    }}
    .config-panel {{
      background: rgba(15, 23, 42, 0.4);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 14px 18px;
      font-size: 0.85rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
    }}
    .footer {{
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
        <h1>✈️ Telegram Multi-Account Broadcast</h1>
        <p>Continuous Render Web Service · Simultaneous 24/7 Workers</p>
      </div>
      <div class="badge">
        <span class="dot" style="background: {badge_color}; box-shadow: 0 0 10px {badge_color};"></span>
        <span style="color: {badge_color};">{badge_text}</span>
      </div>
    </div>

    <div class="global-stats">
      <div class="global-stat-card">
        <div class="stat-label">Total Messages Sent</div>
        <div class="stat-val" style="color: #38bdf8;">{total_sent}</div>
      </div>
      <div class="global-stat-card">
        <div class="stat-label">Active Accounts</div>
        <div class="stat-val">{active_accounts_count} <span style="font-size: 0.85rem; color: var(--text-muted); font-weight: normal;">(of {total_accounts_count})</span></div>
      </div>
      <div class="global-stat-card">
        <div class="stat-label">Service Uptime</div>
        <div class="stat-val">{uptime_str}</div>
      </div>
    </div>

    <div class="section-title">Isolated Account Workers (Running Simultaneously)</div>
    <div class="accounts-grid">
      {account_cards_html}
    </div>

    <div class="config-panel">
      <span style="color: var(--text-muted);">Config Source:</span>
      <span><a href="{config_url}" target="_blank" style="color: #38bdf8; text-decoration: none;">{config_url}</a></span>
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
        path = self.path.split("?")[0]

        if path in ("/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = json.dumps({
                "status": "healthy",
                "state": STATUS.get("state", "unknown"),
                "total_sent": STATUS.get("total_sent", 0),
                "uptime_seconds": int(time.time() - STATUS.get("start_time", time.time())),
                "active_accounts": [
                    {"id": aid, "state": a.get("state"), "account": a.get("account")}
                    for aid, a in STATUS.get("accounts", {}).items()
                ]
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
        # Suppress routine GET logs to prevent cluttering console
        return


# ─────────────────────────────────────────────────────────────────────
# 4. Main Service Execution
# ─────────────────────────────────────────────────────────────────────

def main():
    # 1. Start the multi-account Telegram supervisor in background daemon thread
    worker_thread = threading.Thread(
        target=background_worker_loop,
        daemon=True,
        name="MultiAccountSupervisor"
    )
    worker_thread.start()

    # 2. Start the self-ping keep alive worker
    keep_alive_thread = threading.Thread(
        target=keep_alive_loop,
        daemon=True,
        name="KeepAliveWorker"
    )
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
