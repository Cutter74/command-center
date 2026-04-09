#!/usr/bin/env python3
"""
mother_reporter.py — Reads health state and asks Mother bot to analyze any issues.
Runs 60s after mother_health.py via cron (minute 1 of every hour).
"""
import json, os, sys, urllib.request

# Pull webhook from mother_health — no secret duplication
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mother_health import WEBHOOK

MOTHER_USER_ID = "1473698168414802089"
STATE = os.path.expanduser("~/.openclaw/mother_health_state.json")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception as e:
        print(f"Cannot read state: {e}")
        return None


def condense(st):
    """Extract only the fields worth sending to Mother for analysis."""
    out = {"status": st.get("issues") or "green"}

    if st.get("restarts"):
        out["local_restarts"] = st["restarts"]
    if st.get("restarts_vps"):
        out["vps_restarts"] = st["restarts_vps"]

    sh = st.get("strategy_health")
    if sh:
        out["strategy"] = {
            "status": sh.get("scan_status"),
            "markets_scanned": sh.get("markets_scanned"),
            "hours_since_scan": sh.get("hours_since_scan"),
        }

    active_stalls = {k: v for k, v in st.get("stall_counts", {}).items() if v > 0}
    if active_stalls:
        out["active_stalls"] = active_stalls

    return out


def post(msg):
    body = json.dumps({"content": msg}).encode()
    req = urllib.request.Request(
        WEBHOOK, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mother-Reporter/1.0"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print("Reporter message sent.")
    except Exception as e:
        print(f"Webhook failed: {e}")
        sys.exit(1)


def main():
    force = "--test" in sys.argv
    st = load_state()
    if st is None:
        sys.exit(1)

    status = st.get("issues") or ""
    if not force and status not in ("red", "yellow"):
        print(f"Status is {status or 'green'} — skipping.")
        return

    level = status.upper() if status else "GREEN (test)"
    payload = json.dumps(condense(st), indent=2)

    msg = (
        f"<@{MOTHER_USER_ID}> — `mother_health` just flagged **{level}** on Cutter74. "
        f"Here's the condensed state — what's the likely cause and what should I do?\n"
        f"```json\n{payload}\n```"
    )
    post(msg)


if __name__ == "__main__":
    main()
