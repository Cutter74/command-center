#!/usr/bin/env python3
"""
Codex OAuth auto-refresh — runs daily at 03:00 UTC via cron.
Refreshes any openai-codex profile with <48h remaining.
Alerts Mother's Discord on any issue; silent on "all good".
"""
import json, time, pathlib, subprocess, sys, os, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone

CONTAINER = "openclaw-openclaw-gateway-1"
AUTH_PATH = "/home/node/.openclaw/agents/main/agent/auth-profiles.json"
LOG_FILE = pathlib.Path.home() / "mother-scripts" / "codex_refresh.log"
REFRESH_THRESHOLD_HOURS = 48  # refresh if <48h remaining
MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB rotation

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = "https://auth.openai.com/oauth/token"

WEBHOOK = os.environ.get("MOTHER_HEALTH_WEBHOOK_URL") or os.environ.get("MOTHER_WEBHOOK_URL")
if not WEBHOOK:
    raise RuntimeError("MOTHER_HEALTH_WEBHOOK_URL not set — source mother_env.sh first")


def rotate_log():
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
        LOG_FILE.rename(str(LOG_FILE) + ".1")


def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{stamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def discord(title, description, color=0x00ff00):
    """Post embed to Mother's Discord channel — matches mother_health.py discord() pattern."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = json.dumps({
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": "Cutter74-Linux | " + now}
        }]
    }).encode()
    req = urllib.request.Request(
        WEBHOOK, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mother-HealthCheck/1.0"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"Webhook failed: {e}")


def read_auth_profiles():
    """Read auth-profiles.json from container. Returns (raw_data, profiles_dict)."""
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "cat", AUTH_PATH],
        capture_output=True, text=True, check=True
    )
    raw = json.loads(result.stdout)
    return raw, raw["profiles"]


def write_auth_profiles(raw_data):
    """Atomic write: write to .tmp then mv."""
    tmp_json = json.dumps(raw_data, indent=2)
    p = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "bash", "-c",
         f"cat > {AUTH_PATH}.tmp && mv {AUTH_PATH}.tmp {AUTH_PATH}"],
        input=tmp_json, text=True, capture_output=True
    )
    if p.returncode != 0:
        raise RuntimeError(f"write failed: {p.stderr}")


def refresh_one(profile_id, profile):
    """
    Refresh a single Codex profile.
    Profile fields: access, refresh, expires (ms epoch).
    Token endpoint returns: access_token, refresh_token, expires_in (seconds).
    """
    rt = profile.get("refresh")
    if not rt:
        return False, "missing refresh token"

    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": CLIENT_ID,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        resp_data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"
    except Exception as e:
        return False, str(e)

    if not resp_data.get("access_token") or not resp_data.get("refresh_token"):
        return False, f"response missing fields: {list(resp_data.keys())}"

    profile["access"] = resp_data["access_token"]
    profile["refresh"] = resp_data["refresh_token"]
    # expires_in is in seconds; store as milliseconds epoch (matching gateway format)
    new_expires_ms = int(time.time() * 1000) + int(resp_data.get("expires_in", 600)) * 1000
    profile["expires"] = new_expires_ms
    expiry_str = datetime.fromtimestamp(new_expires_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return True, f"new expiry {expiry_str}"


def main():
    rotate_log()
    log("=== Codex OAuth refresh run started ===")

    try:
        raw_data, profiles = read_auth_profiles()
    except Exception as e:
        log(f"FATAL: could not read auth-profiles.json: {e}")
        discord(
            "🚨 Codex refresh FAILED — cannot read auth-profiles",
            f"Could not read `{AUTH_PATH}` from `{CONTAINER}`:\n```\n{e}\n```\n\nManual wizard may be needed.",
            color=0xff0000
        )
        sys.exit(1)

    refreshed = []
    skipped = []
    failed = []
    now_ms = int(time.time() * 1000)
    threshold_ms = REFRESH_THRESHOLD_HOURS * 3600 * 1000

    for profile_id, profile in list(profiles.items()):
        if "openai-codex" not in profile_id.lower():
            continue
        expires_ms = int(profile.get("expires", 0))
        remaining_hours = (expires_ms - now_ms) / (3600 * 1000)
        if expires_ms - now_ms > threshold_ms:
            skipped.append(f"{profile_id}: {remaining_hours:.1f}h remaining (>{REFRESH_THRESHOLD_HOURS}h)")
            log(f"SKIP {profile_id}: {remaining_hours:.1f}h remaining")
            continue
        log(f"REFRESH {profile_id}: {remaining_hours:.1f}h remaining, refreshing...")
        ok, info = refresh_one(profile_id, profile)
        if ok:
            refreshed.append(f"{profile_id}: {info}")
            log(f"OK   {profile_id}: {info}")
        else:
            failed.append(f"{profile_id}: {info}")
            log(f"FAIL {profile_id}: {info}")

    if refreshed:
        try:
            write_auth_profiles(raw_data)
            log("Atomic write succeeded")
        except Exception as e:
            log(f"FATAL: atomic write failed: {e}")
            discord(
                "🚨 Codex refresh FAILED — write error",
                "Refresh succeeded for:\n" + "\n".join(f"• {r}" for r in refreshed) +
                f"\n\nBut atomic write to `{AUTH_PATH}` failed:\n```\n{e}\n```",
                color=0xff0000
            )
            sys.exit(1)
        result = subprocess.run(
            ["bash", "-c", "cd /home/guest74-linux/openclaw && docker compose down && docker compose up -d"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            log("Gateway restarted OK")
        else:
            log(f"Gateway restart warning: {result.stderr[:200]}")

    if failed:
        body = "**FAILED:**\n" + "\n".join(f"• {f}" for f in failed)
        if refreshed:
            body += "\n\n**Also refreshed:**\n" + "\n".join(f"• {r}" for r in refreshed)
        if skipped:
            body += "\n\n**Skipped (healthy):**\n" + "\n".join(f"• {s}" for s in skipped)
        body += "\n\n⚠️ Manual wizard may be needed."
        discord("🚨 Codex OAuth refresh — partial failure", body, color=0xff0000)
        log(f"Alert sent: {len(failed)} failure(s)")
        sys.exit(1)
    elif refreshed:
        body = f"Refreshed {len(refreshed)} Codex profile(s):\n" + "\n".join(f"• {r}" for r in refreshed)
        if skipped:
            body += "\n\n_Skipped (healthy):_\n" + "\n".join(f"• {s}" for s in skipped)
        discord("✅ Codex OAuth auto-refreshed", body, color=0x00ff00)
        log("Alert sent: refresh succeeded")
    else:
        log(f"No refresh needed. Skipped: {skipped}")

    log("=== Run complete ===")


if __name__ == "__main__":
    main()
