import os
import sys
import json
import time
import random
import functools
import urllib.request
import re

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
# Telegram Credentials & Configuration (Environment variable with fallback)
# ─────────────────────────────────────────────────────────────────────
API_ID   = int(os.environ.get("TELEGRAM_API_ID", 32821627))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "45a260ea58881b721d909c74e40adcbd")
SESSION  = os.environ.get("TELEGRAM_SESSION", "1BVtsOGwBuxMHWzkueTGRPH2xPTwlXvd75TWEcTdFPjJsvt4I2sPE8pEUASgULRIvDQpSzJHkTWNFy666oURDInCgTyiT-c26xbsVAiC0iww_btubS3cXnht8Sv7HJEGaYY-BsL6oPqP-Hj_CsYK4IWKla_SFar8Og4LODCcW72q1nycAH_A6HBk5PxzHfadwHH8N8VP4nRDVYoC75KX59USfLYokElGlWDXkQyhw7K9dG1cZYlKo4p7cjaMP1XzG44qRikWrRqmpyoA1WssVTv2umdRlxftLGsfa81Ijz_BtLu_CAolyswiHaAwwGKEwXQDxseSaCjSj8aRli107Ms_dnsFyMJA=")

CONFIG_URL = os.environ.get("CONFIG_URL", "https://cdn.jisanfx.top/teleauto/config.json")
CONFIG_REFRESH_SECONDS = 30 * 60  # Check remote config every 30 minutes

# Shared telemetry dictionary accessible by the HTTP health server
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
    "interval": "unknown"
}


# ─────────────────────────────────────────────────────────────────────
# Remote Configuration Loader (Strictly URL only)
# ─────────────────────────────────────────────────────────────────────

def fetch_remote_config():
    """Fetch configuration strictly from the remote CDN URL."""
    try:
        req = urllib.request.Request(
            CONFIG_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                raw_text = resp.read().decode("utf-8")
                data = json.loads(raw_text)
                return data
    except Exception as e:
        print(f"[!] Network error fetching config from {CONFIG_URL}: {e}")
    return None


def sanitize_config(config):
    """Normalize and sanitize config fields."""
    msg = config.get("message")
    if isinstance(msg, list):
        config["message"] = "\n".join(msg).strip()
    elif msg is not None:
        config["message"] = str(msg).strip()
    return config


def load_config(retry=False):
    """Load configuration strictly from the remote URL."""
    print(f"[*] Fetching configuration from: {CONFIG_URL} ...")
    while True:
        remote_cfg = fetch_remote_config()

        if remote_cfg and remote_cfg.get("message"):
            config = sanitize_config(remote_cfg)
            print("[+] Successfully loaded remote configuration from URL.")
            return config

        if not retry:
            print(f"[!] Error: Could not reach or load configuration from {CONFIG_URL}.")
            print("[!] No local or default config is allowed. Exiting.")
            sys.exit(1)

        print(f"[!] Warning: Remote config fetch failed. Retrying in 15 seconds...", flush=True)
        time.sleep(15)


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


def get_writable_groups(client):
    """Fetch all groups where the account can send messages."""
    print("\n[*] Fetching writable groups ...")
    writable = []
    for dialog in client.iter_dialogs():
        if dialog.is_group and can_send_message(dialog):
            writable.append(dialog)
    print(f"[+] Found {len(writable)} writable group(s).\n")
    return writable


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


# ─────────────────────────────────────────────────────────────────────
# Core send with msg-limit enforcement
# ─────────────────────────────────────────────────────────────────────

sent_ids: dict[int, list[int]] = {}


def send_and_manage(client, dialog, message, limit):
    """
    Send message to dialog.
    Returns: (status: str, wait_seconds: int)
      status in ('OK', 'HOLD', 'FAIL')
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

        print(f"  [FAIL] {name} -> {ex}")
        return "FAIL", 0

    if sent_msg is None:
        return "FAIL", 0

    # ── Track & enforce message limit ─────────────────────────────────
    if limit > 0:
        ids = sent_ids.setdefault(gid, [])
        ids.append(sent_msg.id)

        while len(ids) > limit:
            old_id = ids.pop(0)
            try:
                client.delete_messages(gid, [old_id])
                print(f"  [DEL]  Removed old msg #{old_id} from {name}")
            except MessageDeleteForbiddenError:
                print(f"  [WARN] No permission to delete msg #{old_id} in {name}")
            except Exception as ex:
                print(f"  [WARN] Could not delete msg #{old_id}: {ex}")

    return "OK", 0


# ─────────────────────────────────────────────────────────────────────
# Smart Independent Scheduler (Instant Send on Hold Expiry)
# ─────────────────────────────────────────────────────────────────────

def run_broadcast(client, groups, config):
    """
    Smart scheduler:
    - If no hold: waits normal interval (e.g. 3-5m).
    - If hold is smaller than normal interval: sends on next normal round.
    - If hold is LARGER than normal interval: sends INSTANTLY as soon as hold expires!
    - Syncs configuration from remote URL every 30 minutes.
    """
    last_refresh_time = time.time()

    def get_params(cfg):
        msg = cfg.get("message", "")
        lim = int(cfg.get("message_limit_per_group", 0) or 0)
        rng = cfg.get("interval_minutes", "3-5")
        rnd = int(cfg.get("rounds", 0) or 0)
        tgt = filter_groups(groups, cfg.get("target_groups", "all"))
        p_min, p_max = parse_range(rng)
        return msg, lim, rng, rnd, tgt, p_min, p_max

    message, limit, raw_interval, rounds, target, min_min, max_min = get_params(config)

    if not target:
        print("[!] No matching target groups found. Aborting.")
        return

    # Track per-group schedules: { gid: timestamp_when_eligible }
    next_eligible_time = {d.id: 0.0 for d in target}
    is_large_hold = {d.id: False for d in target}
    send_count = {d.id: 0 for d in target}

    print("=" * 70)
    print("  SMART INDEPENDENT BROADCAST SCHEDULER")
    print(f"  Strict URL Config : {CONFIG_URL}")
    print(f"  Refresh Rate      : Every 30 minutes")
    print("=" * 70)
    print(f"[*] Target Groups    : {len(target)} of {len(groups)} group(s)")
    for i, d in enumerate(target, 1):
        print(f"    {i}. {safe(d.name)} (ID: {d.id})")
    print(f"[*] Normal Interval  : {min_min}-{max_min} min" if min_min != max_min else f"[*] Normal Interval  : {min_min} min")
    print(f"[*] Hold Policy      : Large hold (> interval) -> Send INSTANTLY upon expiry")
    print(f"                       Small hold (<= interval) -> Send at normal round")
    print(f"[*] Message Limit    : {'Keep last ' + str(limit) if limit > 0 else 'Unlimited (no deletion)'}")
    print(f"[*] Total Rounds     : {'Forever (infinite loop)' if rounds == 0 else rounds}")
    print(f"[*] Broadcast Text   :\n---\n{safe(message)}\n---")
    print("[*] Scheduler active. Press Ctrl+C to stop.\n")

    last_waiting_reported = 0.0

    try:
        while True:
            now = time.time()

            # ── 30-Minute Remote Config Sync ──────────────────────────
            if now - last_refresh_time >= CONFIG_REFRESH_SECONDS:
                print(f"\n[*] [30m Sync] Checking for latest config at {CONFIG_URL} ...")
                new_data = fetch_remote_config()
                last_refresh_time = now
                if new_data and new_data.get("message"):
                    new_cfg = sanitize_config(new_data)
                    changed = False
                    for key in ["message", "message_limit_per_group", "interval_minutes", "rounds", "target_groups"]:
                        if new_cfg.get(key) != config.get(key):
                            changed = True
                            print(f"    - Updated '{key}': {config.get(key)} -> {new_cfg.get(key)}")

                    if changed:
                        config = new_cfg
                        message, limit, raw_interval, rounds, target, min_min, max_min = get_params(config)
                        # Ensure any newly added groups have an eligibility timestamp
                        for d in target:
                            if d.id not in next_eligible_time:
                                next_eligible_time[d.id] = 0.0
                                is_large_hold[d.id] = False
                                send_count[d.id] = 0
                        print("[+] Configuration successfully updated from remote URL!\n")
                    else:
                        print("[*] Configuration is up to date (no changes at URL).\n")
                else:
                    print("[WARN] Could not retrieve remote config update. Retaining active config.\n")

            # ── Find groups ready to send NOW ─────────────────────────
            ready_groups = [d for d in target if now >= next_eligible_time.get(d.id, 0.0)]

            if ready_groups:
                for d in ready_groups:
                    gid = d.id
                    name = safe(d.name)
                    was_large_hold = is_large_hold.get(gid, False)

                    # Compute a fresh normal interval
                    chosen_min = random.uniform(min_min, max_min)
                    normal_interval_sec = chosen_min * 60

                    status, wait_seconds = send_and_manage(client, d, message, limit)
                    send_time = time.time()

                    if status == "OK":
                        send_count[gid] += 1
                        STATUS["total_sent"] += 1
                        STATUS["last_sent_time"] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
                        is_large_hold[gid] = False
                        next_eligible_time[gid] = send_time + normal_interval_sec
                        next_t = time.strftime('%H:%M:%S', time.localtime(next_eligible_time[gid]))

                        if was_large_hold:
                            print(f"  [OK]   {name} [HOLD EXPIRED -> SENT INSTANTLY!] (next round: {next_t})")
                        else:
                            print(f"  [OK]   {name} (next round in {format_duration(normal_interval_sec)} at {next_t})")

                    elif status == "HOLD":
                        # Compare hold duration to normal interval
                        if wait_seconds > normal_interval_sec:
                            # LARGE HOLD: send INSTANTLY when hold expires!
                            is_large_hold[gid] = True
                            next_eligible_time[gid] = send_time + wait_seconds + 1  # 1s safety buffer
                            instant_t = time.strftime('%H:%M:%S', time.localtime(next_eligible_time[gid]))
                            print(f"  [HOLD] {name} – SlowMode {format_duration(wait_seconds)} [LARGE HOLD: will send INSTANTLY at {instant_t}]")
                        else:
                            # SMALL HOLD: hold is shorter than normal interval, send on normal round
                            is_large_hold[gid] = False
                            next_eligible_time[gid] = send_time + normal_interval_sec
                            next_t = time.strftime('%H:%M:%S', time.localtime(next_eligible_time[gid]))
                            print(f"  [HOLD] {name} – SlowMode {format_duration(wait_seconds)} (within normal round: next at {next_t})")

                    else:
                        # FAIL: retry after small backoff (1 normal interval)
                        is_large_hold[gid] = False
                        next_eligible_time[gid] = send_time + normal_interval_sec

                # Reset status report timestamp so next wait line can be displayed cleanly
                last_waiting_reported = 0.0

            # ── Check if all rounds completed (when rounds > 0) ───────
            if rounds != 0 and all(send_count.get(d.id, 0) >= rounds for d in target):
                print(f"\n[*] All groups completed {rounds} round(s). Exiting.")
                break

            # ── Sleep until the earliest group is ready ───────────────
            earliest_time = min(next_eligible_time.get(d.id, 0.0) for d in target)
            remaining_sleep = max(0.0, earliest_time - time.time())

            if remaining_sleep > 0:
                # Find which group is up next
                next_d = min(target, key=lambda d: next_eligible_time.get(d.id, 0.0))
                next_t = time.strftime('%H:%M:%S', time.localtime(earliest_time))
                tag = "[HOLD EXPIRING -> INSTANT SEND]" if is_large_hold.get(next_d.id, False) else "[NORMAL ROUND]"

                # Print waiting status once per wait block
                if time.time() - last_waiting_reported > 60:
                    print(f"[*] Next: {safe(next_d.name)} in {format_duration(remaining_sleep)} at {next_t} {tag}")
                    last_waiting_reported = time.time()

                # Sleep in short slices (max 1.0s) for responsive Ctrl+C and 30m checks
                sleep_slice = min(remaining_sleep, 1.0)
                time.sleep(sleep_slice)
            else:
                time.sleep(0.5)

            STATUS["last_heartbeat"] = time.time()

    except KeyboardInterrupt:
        print("\n\n[!] Stopped by user.")


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────

def main(loop_mode=False):
    STATUS["state"] = "fetching_config"
    config = load_config(retry=loop_mode)

    print("[*] Connecting to Telegram ...")
    STATUS["state"] = "connecting"
    with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        me = client.get_me()
        account_name = f"{me.first_name} (@{me.username or 'NoUsername'})"
        STATUS["account"] = account_name
        print(f"[+] Connected as: {account_name}")

        groups = get_writable_groups(client)
        STATUS["writable_groups"] = len(groups)
        if not groups:
            print("[!] No writable groups found.")
            STATUS["state"] = "no_groups"
            if loop_mode:
                time.sleep(60)
                return
            sys.exit(0)

        STATUS["state"] = "broadcasting"
        run_broadcast(client, groups, config)


if __name__ == "__main__":
    main()
