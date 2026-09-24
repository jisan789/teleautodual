import os
import sys
import json
import time
import random
import functools
import urllib.request
import re
import threading
import asyncio

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Force unbuffered output for real-time logs
print = functools.partial(print, flush=True)

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel, Chat,
    ChatAdminRights,
    ChannelParticipantCreator,
    ChannelParticipantAdmin,
)
from telethon.errors import (
    FloodWaitError,
    SlowModeWaitError,
    MessageDeleteForbiddenError,
)

# ─────────────────────────────────────────────────────────────────────
# 1. Telegram Account Credentials (Set Inside Code)
# ─────────────────────────────────────────────────────────────────────
# Put your 2 accounts' API_ID, API_HASH, and SESSION ("Season key") here.
# (Optional environment variable overrides are also supported).
# ─────────────────────────────────────────────────────────────────────
ACCOUNT_CREDENTIALS = {
    "account_1": {
        "name": "Account 1",
        "api_id": int(os.environ.get("TELEGRAM_API_ID_1") or os.environ.get("TELEGRAM_API_ID") or 35126153),
        "api_hash": os.environ.get("TELEGRAM_API_HASH_1") or os.environ.get("TELEGRAM_API_HASH") or "a95d613cee72019ccb984d8686c3e592",
        "session": os.environ.get("TELEGRAM_SESSION_1") or os.environ.get("TELEGRAM_SEASON_1") or os.environ.get("TELEGRAM_SESSION") or "1BVtsOGwBuwLZqUsI-S3XhZKvg5ni0P_9C5pEKvbDrObmzLkpd_AgTREGKXlMdkdSNHsJyGbu_qRGYKUn0se7xXBUxKznV3VNKblWiEayxIcrn38yoks93sIXLsDIvIm_NE_nSX6acnRR0SE606evD8MpX2gJgKbMHncJD_6QqC77nbDC-HErAHpIuVOLfrBtW2VOeAPrxNU_YRETkHJJAZ-BXOJQu_2vBs2gzYw0AzKtu237G4tI0NdlwWdwtxPOwJYdmz50PCrtNMqQg1BfX29dqKghiO8rcmiCSZcOOMbp2r8KIrlBp21n7UVw96QXphvpB-7sVg4tbmx8EnJk-5mmrqr5Bxg=",
    },
    "account_2": {
        "name": "Account 2",
        "api_id": int(os.environ.get("TELEGRAM_API_ID_2") or 33591633),
        "api_hash": os.environ.get("TELEGRAM_API_HASH_2") or "a4e0dc8c681a8c6afe6124140b163768",
        "session": os.environ.get("TELEGRAM_SESSION_2") or os.environ.get("TELEGRAM_SEASON_2") or "1BVtsOGwBu3yGzPNv-fsFflxZZfVVJ51fASvfmQMW2NTxCNC5oLykiG9j_0h8bsEIKv1PfT3snFQ-h9QdvbjwGQkRjR-0V4mwGOepOEwU5f54RyvLUhIass2gU5rH98oeCv_Zg3JDYHR1sSI14onOqbLbCks3TIBgfsfReIC023RXvZxK6lUOtm-7ZjvKg4F8Gmphw21c4vM0IU6-aFyEgEy2CDBlGAOnESZI_qWWo5hQfrYZMJH0VUGXUFVvTjr-b0B8GjQ5L6qX1lhk2ZufQEAPWdlzoMegy026V5gEheXgsmPmpiUh6rUowDJoJhedwYR8TDZe5aO6X46fUE1ru5uvUSP37ls=",
    },
}

# ─────────────────────────────────────────────────────────────────────
# 2. Remote Configuration URL
# Broadcast parameters (message, limit, interval, rounds, target groups)
# are pulled from this URL.
# ─────────────────────────────────────────────────────────────────────
CONFIG_URL = os.environ.get("CONFIG_URL", "https://cdn.jisanfx.top/teleauto/dualconfig,json")
CONFIG_REFRESH_SECONDS = 30 * 60  # Synchronize with remote URL every 30 minutes

# Shared telemetry dictionary accessible by HTTP server and dashboard
STATUS = {
    "state": "starting",
    "account": None,
    "writable_groups": 0,
    "target_groups": 0,
    "total_sent": 0,
    "last_sent_time": None,
    "last_error": None,
    "start_time": time.time(),
    "last_heartbeat": time.time(),
    "config_url": CONFIG_URL,
    "interval": "unknown",
    "accounts": {},
}

SHARED_CONFIG = {
    "active": None,
    "last_sync": 0.0,
}

# ─────────────────────────────────────────────────────────────────────
# 3. Cross-Account Anti-Overlap Gap (Minimum 2 Minutes Per Group)
# Ensures Account 1 and Account 2 never post to the same group within
# MIN_CROSS_ACCOUNT_GAP_SECONDS (2 minutes) of each other.
# ─────────────────────────────────────────────────────────────────────
MIN_CROSS_ACCOUNT_GAP_SECONDS = int(os.environ.get("MIN_CROSS_ACCOUNT_GAP_SECONDS", 120))  # 2 minutes gap
SHARED_GROUP_LAST_SENT: dict[int, tuple[str, float]] = {}  # { gid: (account_name, timestamp) }
SHARED_GROUP_SENDING: set[int] = set()  # { gid currently in flight }
GROUP_COOLDOWN_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────────────
# Remote Configuration Loader & Parser
# ─────────────────────────────────────────────────────────────────────

def fetch_remote_config():
    """Fetch configuration from remote CDN URL (or local file for testing)."""
    if CONFIG_URL.startswith("file://") or os.path.isfile(CONFIG_URL):
        try:
            local_path = CONFIG_URL.replace("file://", "")
            with open(local_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error reading local config {CONFIG_URL}: {e}")
            return None

    req = urllib.request.Request(
        CONFIG_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    try:
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    raw_text = resp.read().decode("utf-8")
                    return json.loads(raw_text)
        except Exception:
            # Resilient fallback if host environment lacks updated root CA certificates
            import ssl
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                if resp.status == 200:
                    raw_text = resp.read().decode("utf-8")
                    return json.loads(raw_text)
    except Exception as e:
        print(f"[!] Network error fetching config from {CONFIG_URL}: {e}")
    return None


def parse_account_data(full_config, acc_key):
    """
    Pairs credentials from ACCOUNT_CREDENTIALS with broadcast settings
    from the remote config JSON for account_1 or account_2.

    Broadcast settings from JSON:
      - message
      - message_limit_per_group
      - interval_minutes
      - rounds
      - target_groups
    """
    creds = ACCOUNT_CREDENTIALS.get(acc_key, {})
    index = 1 if "1" in acc_key else 2

    # Find account-specific block in remote JSON
    acc_block = {}
    if isinstance(full_config, dict):
        candidates = [acc_key, f"acc_{index}", f"account_{index}", str(index)]
        for c in candidates:
            if c in full_config and isinstance(full_config[c], dict):
                acc_block = full_config[c]
                break
            if "accounts" in full_config and isinstance(full_config["accounts"], dict):
                if c in full_config["accounts"] and isinstance(full_config["accounts"][c], dict):
                    acc_block = full_config["accounts"][c]
                    break

    # Message text
    raw_msg = acc_block.get("message")
    if isinstance(raw_msg, list):
        message_str = "\n".join(str(m) for m in raw_msg).strip()
    elif raw_msg is not None:
        message_str = str(raw_msg).strip()
    else:
        # Fallback to top-level message if legacy flat JSON
        top_msg = full_config.get("message", "") if isinstance(full_config, dict) else ""
        if isinstance(top_msg, list):
            message_str = "\n".join(str(m) for m in top_msg).strip()
        else:
            message_str = str(top_msg).strip()

    # Broadcast settings
    interval = str(acc_block.get("interval_minutes") or (full_config.get("interval_minutes") if isinstance(full_config, dict) else None) or "3-5")
    limit = int(acc_block.get("message_limit_per_group") or (full_config.get("message_limit_per_group") if isinstance(full_config, dict) else None) or 0)
    rounds = int(acc_block.get("rounds") or (full_config.get("rounds") if isinstance(full_config, dict) else None) or 0)
    targets = acc_block.get("target_groups") or (full_config.get("target_groups") if isinstance(full_config, dict) else None) or "all"

    return {
        "id": acc_key,
        "key": acc_key,
        "name": creds.get("name") or f"Account {index}",
        "api_id": creds.get("api_id", 0),
        "api_hash": creds.get("api_hash", ""),
        "session": creds.get("session", ""),
        "message": message_str,
        "message_limit_per_group": limit,
        "interval_minutes": interval,
        "rounds": rounds,
        "target_groups": targets,
    }


def load_config(retry=False):
    """Load configuration strictly from the remote URL."""
    print(f"[*] Fetching configuration from: {CONFIG_URL} ...")
    while True:
        remote_cfg = fetch_remote_config()

        if remote_cfg and isinstance(remote_cfg, dict):
            print("[+] Successfully loaded remote configuration from URL.")
            SHARED_CONFIG["active"] = remote_cfg
            SHARED_CONFIG["last_sync"] = time.time()
            return remote_cfg

        if not retry:
            print(f"[!] Error: Could not reach or load configuration from {CONFIG_URL}.")
            print("[!] No local config allowed. Exiting.")
            sys.exit(1)

        print(f"[!] Warning: Remote config fetch failed. Retrying in 15 seconds...", flush=True)
        time.sleep(15)


def get_current_config():
    """Get active config, checking for remote updates every 30 minutes."""
    now = time.time()
    if SHARED_CONFIG["active"] is None or (now - SHARED_CONFIG["last_sync"] >= CONFIG_REFRESH_SECONDS):
        new_data = fetch_remote_config()
        if new_data and isinstance(new_data, dict):
            SHARED_CONFIG["active"] = new_data
            SHARED_CONFIG["last_sync"] = now
            print("[*] [Remote Sync] Config checked and synchronized from CDN.")
    return SHARED_CONFIG["active"]


# ─────────────────────────────────────────────────────────────────────
# Helpers & Formatting
# ─────────────────────────────────────────────────────────────────────

def safe(text):
    """Encode text safely for console display."""
    if not text:
        return ""
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("utf"):
            return text
        return text.encode(sys.stdout.encoding or "cp1252", errors="replace").decode(sys.stdout.encoding or "cp1252")
    except Exception:
        return text.encode("ascii", errors="replace").decode("ascii")


def format_duration(seconds):
    """Format seconds into readable duration (e.g. '45s', '3m 20s', '1h 05m')."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s" if s > 0 else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def can_send_message(dialog):
    """Return True if the account can send messages in this dialog."""
    entity = dialog.entity

    if isinstance(entity, Chat):
        return not entity.deactivated and not getattr(entity, 'left', False)

    if isinstance(entity, Channel):
        if not entity.megagroup:
            return False
        if getattr(entity, 'left', False):
            return False
        if entity.default_banned_rights and entity.default_banned_rights.send_messages:
            participant = getattr(dialog, 'participant', None)
            if participant is None:
                return False
            if isinstance(participant, (ChannelParticipantCreator, ChannelParticipantAdmin)):
                rights: ChatAdminRights = getattr(participant, 'admin_rights', None)
                if rights and (rights.post_messages or rights.ban_users):
                    return True
            return False
        return True

    return False


def filter_groups(groups, target_spec):
    """Filter groups based on target_spec ('all' or list of IDs / names)."""
    if not target_spec or target_spec == "all":
        return groups

    if isinstance(target_spec, (int, str)):
        target_spec = [target_spec]

    target_ids = set()
    target_names = set()
    for item in target_spec:
        s_item = str(item).strip()
        if s_item.lstrip('-').isdigit():
            target_ids.add(int(s_item))
        else:
            target_names.add(s_item.lower())

    matched = []
    for g in groups:
        g_name = safe(getattr(g, 'name', '') or getattr(g, 'title', '')).strip().lower()
        if g.id in target_ids or g_name in target_names:
            matched.append(g)

    return matched


def parse_range(raw):
    """Parse '3-5' -> (3.0, 5.0) or float value in minutes."""
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val, val

    raw = str(raw).strip()
    if '-' in raw:
        lo, hi = raw.split('-', 1)
        lo, hi = float(lo.strip()), float(hi.strip())
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi

    val = float(raw)
    return val, val


def update_global_status():
    """Aggregate per-account statistics into the shared STATUS dict for HTTP dashboard."""
    total_sent = 0
    total_writable = 0
    total_target = 0
    acc_labels = []
    states = []
    errors = []
    latest_sent = None

    for acc_id, st in STATUS.get("accounts", {}).items():
        total_sent += st.get("total_sent", 0)
        total_writable += st.get("writable_groups", 0)
        total_target += st.get("target_groups", 0)
        if st.get("account"):
            acc_labels.append(f"{st.get('name')}: {st.get('account')}")
        st_state = st.get("state", "unknown")
        states.append(st_state)
        err = st.get("last_error")
        if err and err != "None":
            errors.append(f"{st.get('name')}: {err}")
        sent_t = st.get("last_sent_time")
        if sent_t and (not latest_sent or sent_t > latest_sent):
            latest_sent = sent_t

    STATUS["total_sent"] = total_sent
    STATUS["writable_groups"] = total_writable
    STATUS["target_groups"] = total_target
    STATUS["account"] = " | ".join(acc_labels) if acc_labels else "Connecting..."
    STATUS["last_sent_time"] = latest_sent
    STATUS["last_error"] = " | ".join(errors) if errors else "None"

    if any(s == "broadcasting" for s in states):
        STATUS["state"] = "broadcasting"
    elif any(s in ("starting", "connecting") for s in states):
        STATUS["state"] = "starting"
    elif any(s == "error" for s in states):
        STATUS["state"] = "error"
    elif all(s == "unconfigured" for s in states):
        STATUS["state"] = "unconfigured"
    else:
        STATUS["state"] = "idle"

    STATUS["last_heartbeat"] = time.time()


# ─────────────────────────────────────────────────────────────────────
# Main Account Worker (Completely Isolated & Concurrently Executed)
# ─────────────────────────────────────────────────────────────────────

class TelegramAccountWorker:
    """
    Dedicated worker for a main Telegram account.
    Maintains complete isolation:
      - Credentials loaded directly from ACCOUNT_CREDENTIALS
      - Broadcast campaign options loaded from remote config JSON
      - Independent Telethon client and connection
      - Independent message ID history for per-group deletion limit
      - Independent schedule, slowmode, and flood timers
      - Independent logging prefix [Account 1] / [Account 2]
    """

    def __init__(self, acc_data):
        self.acc_id = acc_data["id"]
        self.acc_key = acc_data["key"]
        self.name = acc_data["name"]
        self.api_id = acc_data["api_id"]
        self.api_hash = acc_data["api_hash"]
        self.session_str = acc_data["session"]
        self.acc_data = acc_data

        # Independent message tracking for deletion limits
        self.sent_ids: dict[int, list[int]] = {}

        msg_preview = acc_data.get("message", "")
        if len(msg_preview) > 50:
            msg_preview = msg_preview[:50] + "..."

        self.status = {
            "id": self.acc_id,
            "name": self.name,
            "state": "starting",
            "account": None,
            "writable_groups": 0,
            "target_groups": 0,
            "total_sent": 0,
            "last_sent_time": None,
            "last_error": None,
            "interval": acc_data.get("interval_minutes", "3-5"),
            "limit": acc_data.get("message_limit_per_group", 0),
            "message_preview": msg_preview,
        }
        STATUS["accounts"][self.acc_id] = self.status

    def log(self, text):
        """Log message with this account's distinct prefix."""
        print(f"[{self.name}] {text}")

    def get_writable_groups(self, client):
        """Fetch all groups where this specific account can send messages."""
        self.log("Fetching writable groups ...")
        writable = []
        for dialog in client.iter_dialogs():
            if dialog.is_group and can_send_message(dialog):
                writable.append(dialog)
        self.log(f"Found {len(writable)} writable group(s).")
        return writable

    def send_and_manage(self, client, dialog, message, limit):
        """
        Send message to dialog and maintain message limit per group.
        Returns: (status: str, wait_seconds: int)
        """
        gid = dialog.id
        name = safe(dialog.name)
        sent_msg = None

        try:
            sent_msg = client.send_message(gid, message)
        except SlowModeWaitError as e:
            wait_s = getattr(e, 'seconds', 0)
            return "HOLD", wait_s
        except FloodWaitError as e:
            wait_s = getattr(e, 'seconds', 0)
            return "HOLD", wait_s
        except Exception as ex:
            err_msg = str(ex)
            if "A wait of" in err_msg and "is required" in err_msg:
                try:
                    m = re.search(r"A wait of (\d+) seconds is required", err_msg)
                    if m:
                        secs = int(m.group(1))
                        return "HOLD", secs
                except Exception:
                    pass
                return "HOLD", 300

            self.log(f"  [FAIL] {name} -> {ex}")
            return "FAIL", 0

        if sent_msg is None:
            return "FAIL", 0

        # Track and enforce message limit per group strictly for this account
        if limit > 0:
            ids = self.sent_ids.setdefault(gid, [])
            ids.append(sent_msg.id)

            while len(ids) > limit:
                old_id = ids.pop(0)
                try:
                    client.delete_messages(gid, [old_id])
                    self.log(f"  [DEL]  Removed old msg #{old_id} from {name}")
                except MessageDeleteForbiddenError:
                    self.log(f"  [WARN] No permission to delete msg #{old_id} in {name}")
                except Exception as ex:
                    self.log(f"  [WARN] Could not delete msg #{old_id}: {ex}")

        return "OK", 0

    def run_broadcast(self, client, groups):
        """
        Smart independent scheduler for this account:
        Uses broadcast campaign options from remote JSON.
        """
        message = self.acc_data.get("message", "")
        limit = self.acc_data.get("message_limit_per_group", 0)
        raw_interval = self.acc_data.get("interval_minutes", "3-5")
        rounds = self.acc_data.get("rounds", 0)
        target = filter_groups(groups, self.acc_data.get("target_groups", "all"))
        min_min, max_min = parse_range(raw_interval)

        self.status["target_groups"] = len(target)
        self.status["interval"] = raw_interval
        self.status["limit"] = limit
        msg_preview = message[:50] + "..." if len(message) > 50 else message
        self.status["message_preview"] = msg_preview
        update_global_status()

        if not target:
            self.log("[!] No matching target groups found. Worker resting for 60s.")
            time.sleep(60)
            return

        next_eligible_time = {d.id: 0.0 for d in target}
        is_large_hold = {d.id: False for d in target}
        send_count = {d.id: 0 for d in target}

        self.log("=" * 60)
        self.log(f"Broadcast Scheduler Active | Targets: {len(target)} of {len(groups)} group(s)")
        for i, d in enumerate(target, 1):
            self.log(f"    {i}. {safe(d.name)} (ID: {d.id})")
        interval_label = f"{min_min}-{max_min} min" if min_min != max_min else f"{min_min} min"
        self.log(f"Interval: {interval_label} | Deletion Limit: {limit} | Rounds: {rounds if rounds > 0 else 'Infinite'}")
        self.log(f"Broadcast Text:\n---\n{safe(message)}\n---")
        self.log("=" * 60)

        last_waiting_reported = 0.0

        while True:
            now = time.time()

            # Refresh parameters if remote config was updated
            active_full_cfg = get_current_config()
            new_acc_data = parse_account_data(active_full_cfg, self.acc_key)

            # Check if this account's campaign parameters changed
            keys_to_check = ["message", "message_limit_per_group", "interval_minutes", "rounds", "target_groups"]
            if any(new_acc_data.get(k) != self.acc_data.get(k) for k in keys_to_check):
                self.log("[+] Detected update to this account's broadcast config in remote JSON!")
                self.acc_data = new_acc_data
                message = self.acc_data.get("message", "")
                limit = self.acc_data.get("message_limit_per_group", 0)
                raw_interval = self.acc_data.get("interval_minutes", "3-5")
                rounds = self.acc_data.get("rounds", 0)
                target = filter_groups(groups, self.acc_data.get("target_groups", "all"))
                min_min, max_min = parse_range(raw_interval)

                self.status["target_groups"] = len(target)
                self.status["interval"] = raw_interval
                self.status["limit"] = limit
                msg_preview = message[:50] + "..." if len(message) > 50 else message
                self.status["message_preview"] = msg_preview

                for d in target:
                    if d.id not in next_eligible_time:
                        next_eligible_time[d.id] = 0.0
                        is_large_hold[d.id] = False
                        send_count[d.id] = 0
                self.log(f"[+] Applied new message & interval ({raw_interval} min).")

            # Find groups eligible to send NOW
            ready_groups = [d for d in target if now >= next_eligible_time.get(d.id, 0.0)]

            if ready_groups:
                # Randomize candidate group order slightly so accounts don't step on identical sequence
                shuffled_groups = list(ready_groups)
                random.shuffle(shuffled_groups)

                for d in shuffled_groups:
                    gid = d.id
                    name = safe(d.name)
                    was_large_hold = is_large_hold.get(gid, False)

                    # ── Check Cross-Account Anti-Overlap Gap (Minimum 2 Minutes per group) ──
                    with GROUP_COOLDOWN_LOCK:
                        last_poster, last_time = SHARED_GROUP_LAST_SENT.get(gid, (None, 0.0))
                        time_since_last = time.time() - last_time

                        if gid in SHARED_GROUP_SENDING:
                            # Another account is actively transmitting to this group right now
                            next_eligible_time[gid] = time.time() + 30.0
                            continue

                        if last_poster and last_poster != self.name and (time_since_last < MIN_CROSS_ACCOUNT_GAP_SECONDS):
                            gap_needed = MIN_CROSS_ACCOUNT_GAP_SECONDS - time_since_last
                            next_eligible_time[gid] = time.time() + gap_needed
                            gap_t = time.strftime('%H:%M:%S', time.localtime(next_eligible_time[gid]))
                            self.log(f"  [GAP]  {name} was posted to by {last_poster} {int(time_since_last)}s ago. Pausing {format_duration(gap_needed)} (next at {gap_t}) to maintain 2m gap.")
                            continue

                        # Mark group in-flight so the other account doesn't send simultaneously
                        SHARED_GROUP_SENDING.add(gid)

                    chosen_min = random.uniform(min_min, max_min)
                    normal_interval_sec = chosen_min * 60

                    try:
                        status, wait_seconds = self.send_and_manage(client, d, message, limit)
                        send_time = time.time()
                    finally:
                        with GROUP_COOLDOWN_LOCK:
                            SHARED_GROUP_SENDING.discard(gid)

                    if status == "OK":
                        with GROUP_COOLDOWN_LOCK:
                            SHARED_GROUP_LAST_SENT[gid] = (self.name, send_time)

                        send_count[gid] += 1
                        self.status["total_sent"] += 1
                        self.status["last_sent_time"] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
                        is_large_hold[gid] = False
                        next_eligible_time[gid] = send_time + normal_interval_sec
                        next_t = time.strftime('%H:%M:%S', time.localtime(next_eligible_time[gid]))
                        update_global_status()

                        if was_large_hold:
                            self.log(f"  [OK]   {name} [HOLD EXPIRED -> SENT INSTANTLY!] (next: {next_t})")
                        else:
                            self.log(f"  [OK]   {name} (next in {format_duration(normal_interval_sec)} at {next_t})")

                    elif status == "HOLD":
                        if wait_seconds > normal_interval_sec:
                            is_large_hold[gid] = True
                            next_eligible_time[gid] = send_time + wait_seconds + 1
                            instant_t = time.strftime('%H:%M:%S', time.localtime(next_eligible_time[gid]))
                            self.log(f"  [HOLD] {name} – SlowMode {format_duration(wait_seconds)} [Will send INSTANTLY at {instant_t}]")
                        else:
                            is_large_hold[gid] = False
                            next_eligible_time[gid] = send_time + normal_interval_sec
                            next_t = time.strftime('%H:%M:%S', time.localtime(next_eligible_time[gid]))
                            self.log(f"  [HOLD] {name} – SlowMode {format_duration(wait_seconds)} (within normal round: next at {next_t})")

                    else:
                        is_large_hold[gid] = False
                        next_eligible_time[gid] = send_time + normal_interval_sec

                last_waiting_reported = 0.0

            # Check if all rounds completed
            if rounds != 0 and all(send_count.get(d.id, 0) >= rounds for d in target):
                self.log(f"All target groups completed {rounds} round(s). Broadcast finished.")
                break

            # Sleep until earliest group ready
            earliest_time = min(next_eligible_time.get(d.id, 0.0) for d in target)
            remaining_sleep = max(0.0, earliest_time - time.time())

            if remaining_sleep > 0:
                next_d = min(target, key=lambda d: next_eligible_time.get(d.id, 0.0))
                next_t = time.strftime('%H:%M:%S', time.localtime(earliest_time))
                tag = "[HOLD EXPIRING -> INSTANT SEND]" if is_large_hold.get(next_d.id, False) else "[NORMAL ROUND]"

                if time.time() - last_waiting_reported > 60:
                    self.log(f"Next: {safe(next_d.name)} in {format_duration(remaining_sleep)} at {next_t} {tag}")
                    last_waiting_reported = time.time()

                sleep_slice = min(remaining_sleep, 1.0)
                time.sleep(sleep_slice)
            else:
                time.sleep(0.5)

    def start_worker_loop(self, loop_mode=True):
        """Thread worker runner with individual error recovery."""
        # Ensure thread has its own dedicated asyncio event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        self.log("Worker thread initialized.")

        # Stagger Account 2 start by 20s to offset initial burst across groups
        if "2" in self.acc_id:
            self.log("Staggering startup by 20s to ensure a clean 2m gap from Account 1...")
            time.sleep(20)

        while True:
            try:
                self.status["state"] = "connecting"
                self.log("Connecting to Telegram...")
                update_global_status()

                with TelegramClient(StringSession(self.session_str), self.api_id, self.api_hash) as client:
                    me = client.get_me()
                    username = f"@{me.username}" if me.username else "NoUsername"
                    self.status["account"] = f"{me.first_name} ({username})"
                    self.status["last_error"] = "None"
                    self.log(f"[+] Connected as: {self.status['account']}")

                    groups = self.get_writable_groups(client)
                    self.status["writable_groups"] = len(groups)
                    update_global_status()

                    if not groups:
                        self.log("[!] No writable groups found. Retrying in 60s...")
                        self.status["state"] = "no_groups"
                        update_global_status()
                        if not loop_mode:
                            return
                        time.sleep(60)
                        continue

                    self.status["state"] = "broadcasting"
                    update_global_status()
                    self.run_broadcast(client, groups)

            except Exception as e:
                import traceback
                err_msg = str(e)
                self.status["state"] = "error"
                self.status["last_error"] = err_msg
                update_global_status()
                self.log(f"[ERROR] Worker exception:\n{traceback.format_exc()}")
                if not loop_mode:
                    break
                self.log("Auto-recovering: restarting this account in 30 seconds...")
                time.sleep(30)


# ─────────────────────────────────────────────────────────────────────
# Multi-Account Coordinator & Entry Point
# ─────────────────────────────────────────────────────────────────────

def is_account_configured(acc):
    """Check if account has required credentials."""
    return bool(acc.get("api_id") and acc.get("api_hash") and acc.get("session"))


def main(loop_mode=False):
    """
    Main supervisor:
    Initializes and runs the 2 main Telegram accounts simultaneously.
    Credentials: read from ACCOUNT_CREDENTIALS inside code.
    Campaign settings: read from CONFIG_URL.
    """
    STATUS["state"] = "fetching_config"
    config = load_config(retry=loop_mode)
    SHARED_CONFIG["active"] = config

    active_workers = []

    for acc_key in ACCOUNT_CREDENTIALS.keys():
        acc_data = parse_account_data(config, acc_key)
        acc_id = acc_data["id"]
        acc_name = acc_data["name"]

        if is_account_configured(acc_data):
            worker = TelegramAccountWorker(acc_data)
            active_workers.append(worker)
        else:
            print(f"[*] {acc_name}: Credentials missing in ACCOUNT_CREDENTIALS. Enter api_id, api_hash, and session inside show_writable_groups.py to activate.")
            STATUS["accounts"][acc_id] = {
                "id": acc_id,
                "name": acc_name,
                "state": "unconfigured",
                "account": "Not configured",
                "writable_groups": 0,
                "target_groups": 0,
                "total_sent": 0,
                "last_sent_time": None,
                "last_error": "Add api_id, api_hash, and session inside show_writable_groups.py",
                "interval": acc_data.get("interval_minutes", "3-5"),
                "limit": acc_data.get("message_limit_per_group", 0),
                "message_preview": (acc_data.get("message", "")[:50] + "...") if len(acc_data.get("message", "")) > 50 else acc_data.get("message", ""),
            }

    update_global_status()

    if not active_workers:
        print("\n[!] Error: No active accounts configured.")
        print("[!] Please set api_id, api_hash, and session inside show_writable_groups.py (ACCOUNT_CREDENTIALS).\n")
        STATUS["state"] = "unconfigured"
        if not loop_mode:
            sys.exit(1)
        time.sleep(60)
        return

    print(f"\n{'=' * 65}")
    print(f"  [+] LAUNCHING {len(active_workers)} MAIN ACCOUNT(S) SIMULTANEOUSLY")
    for w in active_workers:
        unique_targets = w.acc_data.get("target_groups", "all")
        unique_int = w.acc_data.get("interval_minutes", "3-5")
        print(f"      - {w.name} (API ID: {w.api_id}) | Interval: {unique_int}m | Targets: {unique_targets}")
    print(f"{'=' * 65}\n")

    threads = []
    for worker in active_workers:
        t = threading.Thread(
            target=worker.start_worker_loop,
            args=(loop_mode,),
            name=f"Worker-{worker.name}",
            daemon=True
        )
        t.start()
        threads.append(t)

    # Supervisor monitor loop
    try:
        while True:
            time.sleep(2)
            update_global_status()
            if not loop_mode and not any(t.is_alive() for t in threads):
                break
    except KeyboardInterrupt:
        print("\n[!] Supervisor interrupted by user. Stopping all account workers...")


if __name__ == "__main__":
    main()
