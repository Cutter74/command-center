#!/usr/bin/env python3
"""
LLMRoute A/B Test Review Script
Run by Mother on March 23, 2026 to review 1-week A/B test results.

Reads /root/llmroute/ab_test.jsonl from the VPS via SSH,
parses the results, and sends a Discord DM summary with recommendation.
"""

import subprocess
import json
import os
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1474871331626680522/jX3Js_uSqH-r-OGgNoWt1QrMSxyHqWoFxUqcEQx1zTnYon2MeJ-EsW19ghKxZi9RAaWS")
VPS = "root@72.62.168.181"
AB_LOG = "/root/llmroute/ab_test.jsonl"


def fetch_ab_log():
    """Read the A/B log from the VPS."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=15", VPS, f"cat {AB_LOG}"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, f"Could not read AB log: {result.stderr}"
        lines = [json.loads(l) for l in result.stdout.strip().split("\n") if l.strip()]
        return lines, None
    except Exception as e:
        return None, str(e)


def analyze(records):
    """Parse A/B records and compute summary stats."""
    total = len(records)
    if total == 0:
        return None

    tiers = defaultdict(int)
    costs = []
    latencies = []
    errors = 0
    cache_hits = 0
    fallbacks = 0

    for r in records:
        new = r.get("new_path", {})
        if not new.get("success"):
            errors += 1
            continue
        tiers[new.get("tier", "unknown")] += 1
        costs.append(new.get("cost_usd", 0))
        latencies.append(new.get("latency_ms", 0))
        if new.get("cache_hit"):
            cache_hits += 1
        if new.get("fallback_used"):
            fallbacks += 1

    successful = total - errors
    avg_cost = sum(costs) / len(costs) if costs else 0
    total_cost = sum(costs)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    return {
        "total_signals": total,
        "successful_routes": successful,
        "errors": errors,
        "tier_distribution": dict(tiers),
        "avg_cost_usd": avg_cost,
        "total_cost_usd": total_cost,
        "avg_latency_ms": avg_latency,
        "cache_hits": cache_hits,
        "fallbacks": fallbacks,
    }


def send_discord(message):
    """Send message to Discord webhook."""
    if not DISCORD_WEBHOOK:
        print("No webhook configured")
        return
    payload = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mother/1.0"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print("Discord alert sent")
    except Exception as e:
        print(f"Discord failed: {e}")


def main():
    print("Starting LLMRoute A/B test review...")

    records, err = fetch_ab_log()

    if err or not records:
        send_discord(
            f"⚠️ **LLMRoute A/B Review** (March 23)\n"
            f"Could not read A/B test log from VPS.\n"
            f"Error: {err or 'No records found'}\n\n"
            f"**Manual check:** `ssh root@72.62.168.181 cat /root/llmroute/ab_test.jsonl`"
        )
        return

    stats = analyze(records)
    if not stats:
        send_discord("⚠️ **LLMRoute A/B Review**: Log file empty — no signals routed during test period.")
        return

    # Build tier breakdown string
    tier_str = " | ".join([f"{t}: {c}" for t, c in sorted(stats["tier_distribution"].items())])

    # Recommendation
    error_rate = stats["errors"] / stats["total_signals"] if stats["total_signals"] > 0 else 0
    if error_rate < 0.1 and stats["successful_routes"] > 0:
        rec = "✅ **READY TO GO LIVE** — error rate acceptable, routing working correctly."
        action = (
            "To go live, run:\n"
            "```\nssh root@72.62.168.181\n"
            "grep -v AB_TEST_MODE /root/llmroute/axis_env.sh > /tmp/env.sh && "
            "echo 'export AB_TEST_MODE=false' >> /tmp/env.sh && "
            "mv /tmp/env.sh /root/llmroute/axis_env.sh\n```"
        )
    elif stats["total_signals"] == 0:
        rec = "⚪ **NO SIGNALS** — no RED signals fired during test period. Safe to go live."
        action = "No data to evaluate. Can go live safely — just flip AB_TEST_MODE=false."
    else:
        rec = f"⚠️ **REVIEW NEEDED** — {error_rate:.0%} error rate. Check logs before going live."
        action = f"Check errors: `ssh root@72.62.168.181 cat /root/llmroute/ab_test.jsonl | python3 -m json.tool`"

    msg = (
        f"🧪 **LLMRoute A/B Test — 7-Day Review** (March 23, 2026)\n\n"
        f"**Signals routed:** {stats['total_signals']}\n"
        f"**Successful:** {stats['successful_routes']} | **Errors:** {stats['errors']}\n"
        f"**Tier distribution:** {tier_str or 'N/A'}\n"
        f"**Avg cost:** ${stats['avg_cost_usd']:.5f} | **Total cost:** ${stats['total_cost_usd']:.4f}\n"
        f"**Avg latency:** {stats['avg_latency_ms']:.0f}ms\n"
        f"**Cache hits:** {stats['cache_hits']} | **Fallbacks:** {stats['fallbacks']}\n\n"
        f"{rec}\n\n"
        f"{action}"
    )

    send_discord(msg)
    print("Review complete.")


if __name__ == "__main__":
    main()
