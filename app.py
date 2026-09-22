import json
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# Read credentials from Render Environment Variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

counter = 0

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.", flush=True)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url, 
            data=data, 
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                print(f"[SUCCESS] Telegram message sent at {time.strftime('%H:%M:%S')}", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram message: {e}", flush=True)


def background_loop():
    global counter
    while True:
        counter += 1
        msg = f"🔔 Render Loop Test\nTick #{counter}\nTime: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        send_telegram_message(msg)

        # Wait 5 minutes (300 seconds) before the next ping
        time.sleep(300)


def keep_alive_loop():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    print(f"[SUCCESS] Keep-alive ping sent at {time.strftime('%H:%M:%S')}", flush=True)
        except Exception as e:
            print(f"[ERROR] Keep-alive ping failed: {e}", flush=True)
        time.sleep(600)


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        response_body = f"Service active. Total messages queued/sent: {counter}\n"
        self.wfile.write(response_body.encode('utf-8'))

    def log_message(self, format, *args):
        return  # Suppress default HTTP server logs


if __name__ == '__main__':
    # Launch loop in a daemon thread
    loop_thread = threading.Thread(target=background_loop, daemon=True)
    loop_thread.start()

    # Launch keep-alive thread in a daemon thread
    keep_alive_thread = threading.Thread(target=keep_alive_loop, daemon=True)
    keep_alive_thread.start()

    # Render web service port binding
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), RequestHandler)
    print(f"Server started on port {port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass