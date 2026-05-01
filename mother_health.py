#!/usr/bin/env python3
import subprocess, json, urllib.request, urllib.error, time, os, sys, socket
from datetime import datetime, timezone

WEBHOOK = "https://discord.com/api/webhooks/1474871331626680522/jX3Js_uSqH-r-OGgNoWt1QrMSxyHqWoFxUqcEQx1zTnYon2MeJ-EsW19ghKxZi9RAaWS"
N8N_REMEDIATION = "https://n8n.srv1242671.hstgr.cloud/webhook/mother-remediation"

SERVICES = []

VPS_HOST = "root@5.78.179.50"
VPS_CONTAINERS = [
    ("n8n VPS", VPS_HOST, "n8n-n8n-1"),
]
CONTAINERS = ["openclaw-openclaw-gateway-1"]
STATE = os.path.expanduser("~/.openclaw/mother_health_state.json")
IB_GATEWAY_STATE = "/home/guest74-linux/mother-scripts/ib_gateway_state.json"

# How many consecutive failures before triggering remediation
STALL_TRIGGER_COUNT = 3

OPENCLAW_BEARER = "ed3107607019a40fd7183eb23237f8ec48a5dd058e1a4a40091fd126cf4d242d"
OPENCLAW_LATENCY_WARN_MS = 2000
OPENCLAW_LATENCY_CRIT_MS = 5000
OPENCLAW_LATENCY_TIMEOUT_S = 8


def ping(name, url, t):
    try:
        start = time.time()
        r = urllib.request.urlopen(urllib.request.Request(url), timeout=t)
        return (name, r.status, round((time.time()-start)*1000), None)
    except urllib.error.HTTPError as e:
        return (name, e.code, 0, str(e))
    except Exception as e:
        return (name, 0, 0, str(e))


def format_uptime(started_at_str):
    """Convert Docker StartedAt timestamp to a human-readable uptime string."""
    if not started_at_str or started_at_str.startswith("0001-"):
        return "n/a"
    try:
        # Truncate sub-second precision; Docker gives nanoseconds
        s = started_at_str[:19] + "+00:00"
        started = datetime.fromisoformat(s)
        delta = datetime.now(timezone.utc) - started
        d, rem = divmod(int(delta.total_seconds()), 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d > 0:
            return f"{d}d {h}h"
        elif h > 0:
            return f"{h}h {m}m"
        else:
            return f"{m}m"
    except:
        return "?"


def openclaw_discord_check():
    """Check OpenClaw liveness via HTTP GET to localhost:18789. Returns True on HTTP 200."""
    try:
        r = urllib.request.urlopen(
            urllib.request.Request("http://localhost:18789/"),
            timeout=10
        )
        result = r.status == 200
        if "--verbose" in sys.argv:
            print(f"[openclaw_discord_check] HTTP {r.status} → {'ok' if result else 'fail'}")
        return result
    except urllib.error.HTTPError as e:
        if "--verbose" in sys.argv:
            print(f"[openclaw_discord_check] HTTPError {e.code}")
        return False
    except Exception as e:
        if "--verbose" in sys.argv:
            print(f"[openclaw_discord_check] exception: {e}")
        return False


def docker_check():
    """Check local Docker containers. Returns list of (name, status, restarts, started_at)."""
    res = []
    fmt = '{"s":"{{.State.Status}}","r":{{.RestartCount}},"t":"{{.State.StartedAt}}"}'
    for c in CONTAINERS:
        try:
            o = subprocess.run(
                ["docker", "inspect", "--format", fmt, c],
                capture_output=True, text=True, timeout=10
            )
            if o.returncode != 0:
                res.append((c, "not found", 0, None))
                continue
            d = json.loads(o.stdout.strip())
            res.append((c, d["s"], d["r"], d.get("t")))
        except:
            res.append((c, "error", 0, None))
    return res


def vps_docker_check():
    """Check VPS Docker containers via SSH. Returns list of (name, status, restarts, started_at)."""
    res = []
    fmt = '{"s":"{{.State.Status}}","r":{{.RestartCount}},"t":"{{.State.StartedAt}}"}'
    for name, host, container in VPS_CONTAINERS:
        try:
            remote_cmd = f"docker inspect '--format={fmt}' {container}"
            out = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=10", host, remote_cmd],
                capture_output=True, text=True, timeout=20
            )
            if out.returncode != 0 or not out.stdout.strip():
                res.append((name, "not found", 0, None))
                continue
            d = json.loads(out.stdout.strip())
            res.append((name, d["s"], d["r"], d.get("t")))
        except Exception as e:
            res.append((name, "error: " + str(e)[:60], 0, None))
    return res


def disk_check():
    """Check local disk usage. Returns [(mount, pct)]."""
    res = []
    try:
        o = subprocess.run(["df", "--output=target,pcent", "/"], capture_output=True, text=True, timeout=10)
        for line in o.stdout.strip().split("\n")[1:]:
            p = line.split()
            if len(p) >= 2:
                res.append((p[0], int(p[1].replace("%", ""))))
    except:
        res.append(("/", -1))
    return res


def vps_disk_check():
    """Check VPS disk usage via SSH. Returns [(mount, pct)]."""
    try:
        out = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=10", VPS_HOST, "df --output=target,pcent /"],
            capture_output=True, text=True, timeout=20
        )
        if out.returncode != 0 or not out.stdout.strip():
            return [("/", -1)]
        res = []
        for line in out.stdout.strip().split("\n")[1:]:
            p = line.split()
            if len(p) >= 2:
                try:
                    res.append((p[0], int(p[1].replace("%", ""))))
                except:
                    pass
        return res if res else [("/", -1)]
    except:
        return [("/", -1)]


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except:
        return {"issues": "", "restarts": {}, "restarts_vps": {}, "tunnel_failures": {}, "stall_counts": {}}


def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(st, f, indent=2)


def discord(msg, color=0x00ff00):
    """Send a simple embed (description only). Used for ad-hoc alerts and heartbeat."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = json.dumps({
        "embeds": [{
            "title": "MOTHER Health Check",
            "description": msg,
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
        print("Webhook failed:", e)


def discord_rich(title, fields, color=0x00cc44, description=None):
    """Send a rich Discord embed with structured fields and a timestamp footer."""
    embed = {
        "title": title,
        "color": color,
        "fields": fields,
        "footer": {"text": "Cutter74-Linux"},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if description:
        embed["description"] = description
    body = json.dumps({"embeds": [embed]}).encode()
    req = urllib.request.Request(
        WEBHOOK, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mother-HealthCheck/1.0"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("Webhook failed:", e)


def trigger_remediation(trigger_type, service_name, alert_type, details=None):
    """Fire the n8n remediation pipeline webhook."""
    payload = json.dumps({
        "trigger_type": trigger_type,
        "service_name": service_name,
        "alert_type": alert_type,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "details": details or {},
        "source_host": "cutter74"
    }).encode()
    req = urllib.request.Request(
        N8N_REMEDIATION,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"Remediation webhook triggered: {trigger_type} / {service_name}")
        return True
    except Exception as e:
        print(f"Remediation webhook failed: {e}")
        return False


def is_tunnel_service(name):
    """VPS services are reached via SSH tunnel — DOWN may mean tunnel not up."""
    return name in ("n8n VPS", "OpenClaw VPS")


def auto_repair(name, status):
    """Try to fix common issues. Returns (fixed, message)"""
    if status in ("exited", "not found", "dead", "created"):
        try:
            out = subprocess.run(["docker", "start", name], capture_output=True, text=True, timeout=30)
            if out.returncode == 0:
                time.sleep(10)
                check = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Status}}", name],
                    capture_output=True, text=True, timeout=10
                )
                new_status = check.stdout.strip()
                if new_status == "running":
                    return (True, "Auto-restarted " + name + " successfully")
                else:
                    return (False, "Tried to restart " + name + " but status is " + new_status)
            else:
                return (False, "Restart command failed for " + name + ": " + out.stderr.strip())
        except Exception as e:
            return (False, "Repair attempt failed for " + name + ": " + str(e))
    return (False, None)


def setup_tunnels():
    """Open SSH tunnels to VPS services"""
    tunnels = []
    try:
        t1 = subprocess.Popen([
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
            "-N", "-L", "15678:127.0.0.1:5678",
            "-L", "28789:127.0.0.1:28789",
            VPS_HOST
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tunnels.append(t1)
        time.sleep(2)
    except Exception as e:
        print("Tunnel setup failed:", e)
    return tunnels


def teardown_tunnels(tunnels):
    for t in tunnels:
        try:
            t.terminate()
        except:
            pass


def check_openclaw_latency():
    """POST a minimal ping to the Codex passthrough server and measure end-to-end latency.

    GREEN  < 1 500 ms  — normal
    YELLOW 1 500–3 000 ms — slow but acceptable
    RED    > 3 000 ms or timeout — LLMRoute will fall back to Sonnet
    Returns (status, latency_ms, embed_line).
    """
    url = "http://localhost:18791/v1/chat/completions"
    payload = json.dumps({
        "model": "openai-codex/gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENCLAW_BEARER}",
        },
        method="POST"
    )
    start = time.time()
    try:
        urllib.request.urlopen(req, timeout=OPENCLAW_LATENCY_TIMEOUT_S)
        latency_ms = round((time.time() - start) * 1000)
    except Exception:
        latency_ms = round((time.time() - start) * 1000)
        status = "red"
        line = f"❌ **Codex Passthrough** — timeout ({latency_ms}ms) — LLMRoute fully on Sonnet fallback"
        print(f"[openclaw_latency] {latency_ms}ms → RED (timeout)")
        discord(
            f"Codex passthrough unreachable/too slow: {latency_ms}ms — LLMRoute fully on Sonnet fallback",
            0xff4444
        )
        return status, latency_ms, line

    if latency_ms < OPENCLAW_LATENCY_WARN_MS:
        status = "green"
        line = f"✅ **Codex Passthrough** — {latency_ms}ms"
    elif latency_ms < OPENCLAW_LATENCY_CRIT_MS:
        status = "yellow"
        line = f"⚠️ **Codex Passthrough** — {latency_ms}ms (SLOW — Hetzner fallback to Sonnet)"
        discord(
            f"Codex passthrough slow: {latency_ms}ms — Hetzner router will fallback to Sonnet",
            0xffa500
        )
    else:
        status = "red"
        line = f"❌ **Codex Passthrough** — {latency_ms}ms (CRITICAL — LLMRoute fully on Sonnet fallback)"
        discord(
            f"Codex passthrough unreachable/too slow: {latency_ms}ms — LLMRoute fully on Sonnet fallback",
            0xff4444
        )

    print(f"[openclaw_latency] {latency_ms}ms → {status.upper()}")
    return status, latency_ms, line


def run():
    st = load()
    tunnel_failures = st.get("tunnel_failures", {})
    has_issues = False
    has_warnings = False

    # ── LOCAL: Cutter74 ─────────────────────────────────────────────
    local_lines = []

    for name, url, t in SERVICES:
        n, status, ms, err = ping(name, url, t)
        if status == 200:
            local_lines.append(f"✅ **{n}** — 200 OK ({ms}ms)")
            tunnel_failures[name] = 0
        else:
            if is_tunnel_service(name):
                tunnel_failures[name] = tunnel_failures.get(name, 0) + 1
                count = tunnel_failures[name]
                if count >= STALL_TRIGGER_COUNT:
                    local_lines.append(f"❌ **{n}** — DOWN ({count} consecutive failures)")
                    has_issues = True
                else:
                    local_lines.append(f"⚠️ **{n}** — tunnel miss {count}/{STALL_TRIGGER_COUNT}")
                    has_warnings = True
            else:
                local_lines.append(f"❌ **{n}** — DOWN")
                has_issues = True

    st["tunnel_failures"] = tunnel_failures

    if openclaw_discord_check():
        local_lines.append("✅ **OpenClaw Local** — Discord: ok")
    else:
        local_lines.append("❌ **OpenClaw Local** — Discord check failed")
        has_issues = True

    lat_status, _lat_ms, lat_line = check_openclaw_latency()
    local_lines.append(lat_line)
    if lat_status == "red":
        has_issues = True
    elif lat_status == "yellow":
        has_warnings = True

    for name, status, restarts, started_at in docker_check():
        prev = st.get("restarts", {}).get(name, 0)
        delta = restarts - prev if restarts >= prev else restarts
        uptime = format_uptime(started_at)
        st.setdefault("restarts", {})[name] = restarts

        if status == "running" and delta == 0:
            local_lines.append(f"✅ **{name}** — running | ↺ {restarts} restarts | up {uptime}")
        elif status == "running" and delta > 0:
            local_lines.append(f"⚠️ **{name}** — running | ↺ +{delta} new restart(s) ({restarts} total) | up {uptime}")
            has_warnings = True
        else:
            fixed, msg = auto_repair(name, status)
            if fixed:
                local_lines.append(f"⚠️ **{name}** — SELF-HEALED (was {status}) | up {format_uptime(None)}")
                has_warnings = True
            else:
                local_lines.append(f"❌ **{name}** — {status}")
                has_issues = True

    # ── VPS: 5.78.179.50 ────────────────────────────────────────────
    vps_lines = []

    for name, status, restarts, started_at in vps_docker_check():
        prev = st.get("restarts_vps", {}).get(name, 0)
        delta = restarts - prev if restarts >= prev else restarts
        uptime = format_uptime(started_at)
        st.setdefault("restarts_vps", {})[name] = restarts

        if status == "running" and delta == 0:
            vps_lines.append(f"✅ **{name}** — running | ↺ {restarts} restarts | up {uptime}")
        elif status == "running" and delta > 0:
            vps_lines.append(f"⚠️ **{name}** — running | ↺ +{delta} new restart(s) ({restarts} total) | up {uptime}")
            has_warnings = True
        else:
            vps_lines.append(f"❌ **{name}** — {status}")
            has_issues = True

    # ── DISK ─────────────────────────────────────────────────────────
    disk_lines = []

    for mount, pct in disk_check():
        if pct >= 90:
            disk_lines.append(f"🔴 **Local {mount}** — {pct}% CRITICAL")
            has_issues = True
        elif pct >= 80:
            disk_lines.append(f"🟡 **Local {mount}** — {pct}% WARNING")
            has_warnings = True
        elif pct >= 0:
            disk_lines.append(f"✅ **Local {mount}** — {pct}%")

    for mount, pct in vps_disk_check():
        if pct >= 90:
            disk_lines.append(f"🔴 **VPS {mount}** — {pct}% CRITICAL")
            has_issues = True
        elif pct >= 80:
            disk_lines.append(f"🟡 **VPS {mount}** — {pct}% WARNING")
            has_warnings = True
        elif pct >= 0:
            disk_lines.append(f"✅ **VPS {mount}** — {pct}%")

    # ── STATUS & SEND ─────────────────────────────────────────────────
    prev_had_issues = bool(st.get("issues"))

    if has_issues:
        color = 0xff4444
        title = "🔴 MOTHER Health — Issues Detected"
    elif has_warnings:
        color = 0xffa500
        title = "🟡 MOTHER Health — Warnings"
    else:
        color = 0x00cc44
        title = "🟢 MOTHER Health — All Systems Green"

    st["issues"] = "red" if has_issues else ("yellow" if has_warnings else "")

    send = has_issues or has_warnings or prev_had_issues or "--verbose" in sys.argv

    if send:
        fields = []
        if local_lines:
            fields.append({"name": "📦  LOCAL — Cutter74", "value": "\n".join(local_lines), "inline": False})
        if vps_lines:
            fields.append({"name": "🌐  VPS — 5.78.179.50", "value": "\n".join(vps_lines), "inline": False})
        if disk_lines:
            fields.append({"name": "💾  Disk Usage", "value": "\n".join(disk_lines), "inline": False})
        discord_rich(title, fields, color)
        print("Report sent:", title)
    else:
        print("All clear — silent")

    save(st)


def heartbeat():
    """Post a 30-minute alive ping to Discord"""
    STATE_HB = os.path.expanduser("~/.openclaw/mother_heartbeat.json")
    try:
        with open(STATE_HB) as f:
            last = json.load(f).get("last", 0)
    except:
        last = 0
    now = time.time()
    if now - last >= 1800:  # 30 minutes
        ok = []
        if openclaw_discord_check():
            ok.append("**OpenClaw Local** ✅ Discord: ok")
        else:
            ok.append("**OpenClaw Local** ❌ Discord check failed")
        for name, status, restarts, started_at in docker_check():
            if status == "running":
                ok.append(f"**{name}** ✅ running (restarts: {restarts})")
        for mount, pct in disk_check():
            if pct >= 0:
                ok.append(f"Disk **{mount}** {pct}%")
        msg = "💓 **Mother Heartbeat** — All systems watched\n" + "\n".join("• " + i for i in ok)
        discord(msg, 0x5865f2)
        with open(STATE_HB, "w") as f:
            json.dump({"last": now}, f)
        print("Heartbeat sent")


def check_strategy_health():
    """Check AXIS_PMS health file for strategy stalls. Triggers n8n remediation on stall."""
    VPS = VPS_HOST
    HEALTH_FILE = "/home/node/.openclaw/workspace/memory/scan-health-axis_pms.json"
    OPTIONS_HEALTH_FILE = "/home/guest74-linux/options_bot/scan-health-options.json"

    if is_weekday():
        try:
            with open(OPTIONS_HEALTH_FILE, 'r') as f:
                options_health = json.load(f)
            last_scan = options_health.get("last_scan_utc", "unknown")
            status = options_health.get("status", "unknown")
            if status.lower() not in ("ok", "healthy"):
                discord(f"OPTIONS BOT HEALTH - Status: {status} | Last scan: {last_scan}", 0xffa500)
        except FileNotFoundError:
            discord("OPTIONS BOT HEALTH - scan-health-options.json not found", 0xffa500)
        except Exception as e:
            discord(f"OPTIONS BOT HEALTH - Failed to read health file: {e}", 0xffa500)
    else:
        print("Options Bot health check — ⏸️ Weekend — skipped")

    NOW = datetime.now(timezone.utc)
    st = load()
    stall_counts = st.get("stall_counts", {})

    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             VPS, "cat", HEALTH_FILE],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 or not result.stdout.strip():
            discord("STRATEGY WATCHDOG - Could not read AXIS_PMS health file", 0xffa500)
            return
        health = json.loads(result.stdout.strip())
    except Exception as e:
        discord("STRATEGY WATCHDOG - Failed to read AXIS_PMS health: " + str(e), 0xffa500)
        return

    scan_status = health.get("scan_status", "UNKNOWN")
    markets_scanned = health.get("markets_scanned", 0)
    signals_found = health.get("signals_found", 0)
    red_signals = health.get("red_signals", 0)
    yellow_signals = health.get("yellow_signals", 0)
    scan_time_str = health.get("scan_time", None)
    hours_since = "unknown"

    if scan_time_str:
        try:
            scan_time = datetime.fromisoformat(scan_time_str.replace("Z", "+00:00"))
            hours_since = round((NOW - scan_time).total_seconds() / 3600, 1)
        except:
            scan_time = None

    st["strategy_health"] = {
        "last_checked": NOW.strftime("%Y-%m-%d %H:%M UTC"),
        "last_scan": scan_time_str or "unknown",
        "scan_status": scan_status,
        "markets_scanned": markets_scanned,
        "signals_found": signals_found,
        "hours_since_scan": hours_since
    }

    alerts = []
    stall_detected = False

    if scan_status != "OK":
        alerts.append("RED: AXIS_PMS scan status is " + scan_status)
        stall_detected = True

    if isinstance(hours_since, float) and hours_since > 5:
        alerts.append("RED: No scan in " + str(hours_since) + "h (cron may be broken)")
        stall_detected = True

    if markets_scanned == 0:
        alerts.append("YELLOW: 0 markets scanned - API may be down")
        stall_detected = True

    if stall_detected:
        stall_counts["axis_pms"] = stall_counts.get("axis_pms", 0) + 1
        count = stall_counts["axis_pms"]

        msg = "AXIS_PMS WATCHDOG ALERT (stall #" + str(count) + ")\n" + "\n".join(alerts)
        msg += "\n\nLast scan: " + (scan_time_str or "unknown")
        msg += "\nMarkets: " + str(markets_scanned) + " | Signals: " + str(signals_found)
        msg += " (RED:" + str(red_signals) + " YELLOW:" + str(yellow_signals) + ")"
        discord(msg, 0xff0000)

        if count >= STALL_TRIGGER_COUNT:
            trigger_remediation(
                trigger_type="strategy_stall",
                service_name=None,
                alert_type="STRATEGY_STALL",
                details={
                    "scan_status": scan_status,
                    "hours_since_scan": hours_since,
                    "markets_scanned": markets_scanned,
                    "consecutive_stalls": count
                }
            )
            stall_counts["axis_pms"] = 0

        print("AXIS_PMS watchdog alert sent (stall #" + str(count) + ")")
    else:
        prev_stall_count = stall_counts.get("axis_pms", 0)
        if prev_stall_count > 0 and scan_status == "OK" and markets_scanned > 0:
            hours_str = f"{hours_since:.1f}h" if isinstance(hours_since, float) else str(hours_since)
            discord(
                f"✅ AXIS_PMS RECOVERED — Scan healthy: {markets_scanned} markets, {hours_str} ago",
                0x00ff00
            )
            print("AXIS_PMS recovered after " + str(prev_stall_count) + " stalls — recovery alert sent")
        stall_counts["axis_pms"] = 0
        print("AXIS_PMS health OK - " + str(markets_scanned) + " markets, " +
              str(signals_found) + " signals, " + str(hours_since) + "h ago")

    st["stall_counts"] = stall_counts
    save(st)


def is_weekday():
    """Returns True if today is Monday–Friday."""
    return datetime.now().weekday() < 5


def load_ibgw_state():
    try:
        with open(IB_GATEWAY_STATE) as f:
            return json.load(f)
    except:
        return {"last_status": "UP", "alerted_down": False}


def save_ibgw_state(state):
    with open(IB_GATEWAY_STATE, "w") as f:
        json.dump(state, f, indent=2)


def check_ibgateway():
    """Check IB Gateway port 7497 with state tracking for clean DOWN/RECOVERED alerts.

    Boot window: daily restart cron fires at 12:00 UTC (6 AM CDT); skip checks for
    10 minutes after to avoid false alarms while the gateway finishes starting up.
    Alert logic: one DOWN alert per incident, one RECOVERED alert when port comes back.
    """
    if not is_weekday():
        print("IB Gateway check — ⏸️ Weekend — skipped")
        return

    NOW_UTC = datetime.now(timezone.utc)
    boot_today = NOW_UTC.replace(hour=12, minute=0, second=0, microsecond=0)
    secs_after_boot = (NOW_UTC - boot_today).total_seconds()
    if 0 <= secs_after_boot <= 600:
        print(f"IB Gateway in boot window ({int(secs_after_boot)}s after restart) — skipping check")
        return

    is_up = False
    try:
        with socket.create_connection(("127.0.0.1", 7497), timeout=5):
            is_up = True
    except Exception:
        is_up = False

    state = load_ibgw_state()
    last_status = state.get("last_status", "UP")
    alerted_down = state.get("alerted_down", False)

    if is_up:
        if last_status == "DOWN":
            discord("✅ IB Gateway RECOVERED — port 7497 healthy", 0x00cc44)
            print("IB Gateway RECOVERED — recovery alert sent")
        else:
            print("IB Gateway: port 7497 healthy")
        save_ibgw_state({"last_status": "UP", "alerted_down": False})
    else:
        if not alerted_down:
            discord("⚠️ IB Gateway DOWN — port 7497 not responding", 0xff4444)
            print("IB Gateway DOWN — alert sent")
        else:
            print("IB Gateway still DOWN — suppressing duplicate alert")
        save_ibgw_state({"last_status": "DOWN", "alerted_down": True})


if __name__ == "__main__":
    tunnels = setup_tunnels()
    try:
        run()
        check_strategy_health()
        check_ibgateway()
    finally:
        teardown_tunnels(tunnels)
