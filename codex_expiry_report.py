#!/usr/bin/env python3
"""Weekly Monday 09:00 UTC — reports current Codex OAuth expiries to Mother's Discord."""
import json, subprocess, os, urllib.request
from datetime import datetime, timezone

CONTAINER = "openclaw-openclaw-gateway-1"
AUTH_PATH = "/home/node/.openclaw/agents/main/agent/auth-profiles.json"

WEBHOOK = os.environ.get("MOTHER_HEALTH_WEBHOOK_URL") or os.environ.get("MOTHER_WEBHOOK_URL")
if not WEBHOOK:
    raise RuntimeError("MOTHER_HEALTH_WEBHOOK_URL not set — source mother_env.sh first")

result = subprocess.run(
    ["docker", "exec", CONTAINER, "cat", AUTH_PATH],
    capture_output=True, text=True
)

now = datetime.now(timezone.utc)
color = 0x3498db

if result.returncode != 0:
    lines = [f"🚨 Could not read auth-profiles.json from `{CONTAINER}`:\n```\n{result.stderr[:300]}\n```"]
    color = 0xff0000
else:
    try:
        data = json.loads(result.stdout)
        profiles = data.get("profiles", {})
    except Exception as e:
        profiles = {}
        lines = [f"🚨 Failed to parse auth-profiles.json: {e}"]
        color = 0xff0000
    else:
        lines = []
        worst_hours = float("inf")
        for pid, p in profiles.items():
            if "openai-codex" not in pid.lower():
                continue
            # expires is stored as milliseconds epoch
            expires_ms = int(p.get("expires", 0))
            expiry = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
            delta_hrs = (expiry - now).total_seconds() / 3600
            worst_hours = min(worst_hours, delta_hrs)
            if delta_hrs > 72:
                emoji = "✅"
            elif delta_hrs > 24:
                emoji = "⚠️"
            else:
                emoji = "🚨"
            lines.append(
                f"{emoji} **{pid}** — expires {expiry.strftime('%b %d %H:%M UTC')} ({delta_hrs:.0f}h remaining)"
            )
        if not lines:
            lines = ["🚨 No `openai-codex` profiles found — something is wrong."]
            color = 0xff0000
        elif worst_hours <= 24:
            color = 0xff0000
        elif worst_hours <= 72:
            color = 0xffa500

timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
body = json.dumps({
    "embeds": [{
        "title": "🗓️ Codex OAuth — Weekly Expiry Report",
        "description": "\n".join(lines),
        "color": color,
        "footer": {"text": "Cutter74-Linux | " + now.strftime("%Y-%m-%d %H:%M")},
        "timestamp": timestamp,
    }]
}).encode()

req = urllib.request.Request(
    WEBHOOK, data=body,
    headers={"Content-Type": "application/json", "User-Agent": "Mother-HealthCheck/1.0"},
    method="POST"
)
try:
    urllib.request.urlopen(req, timeout=10)
    print(f"Weekly expiry report sent: {len(lines)} profile(s)")
except Exception as e:
    print(f"Webhook failed: {e}")
    raise
