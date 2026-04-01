#!/usr/bin/env python3
import subprocess, json, urllib.request, urllib.error, time, os, sys
from datetime import datetime

WEBHOOK = "https://discord.com/api/webhooks/1474871331626680522/jX3Js_uSqH-r-OGgNoWt1QrMSxyHqWoFxUqcEQx1zTnYon2MeJ-EsW19ghKxZi9RAaWS"
N8N_REMEDIATION = "https://n8n.srv1242671.hstgr.cloud/webhook/mother-remediation"

SERVICES = [
    ("OpenClaw Local", "http://localhost:18789", 5),
]

# VPS services checked via SSH docker inspect, not tunnels
VPS_CONTAINERS = [
    ("n8n VPS", "root@5.78.179.50", "n8n-n8n-1"),
    ("OpenClaw VPS", "root@5.78.179.50", "repo-openclaw-gateway-1"),
]
CONTAINERS = ["openclaw-openclaw-gateway-1"]
STATE = os.path.expanduser("~/.openclaw/mother_health_state.json")

# How many consecutive failures before triggering remediation
STALL_TRIGGER_COUNT = 3

def ping(name, url, t):
    try:
        start = time.time()
        r = urllib.request.urlopen(urllib.request.Request(url), timeout=t)
        return (name, r.status, round((time.time()-start)*1000), None)
    except urllib.error.HTTPError as e:
        return (name, e.code, 0, str(e))
    except Exception as e:
        return (name, 0, 0, str(e))

def docker_check():
    res = []
    for c in CONTAINERS:
        try:
            o = subprocess.run(["docker","inspect","--format",'{"s":"{{.State.Status}}","r":{{.RestartCount}}}',c], capture_output=True, text=True, timeout=10)
            if o.returncode != 0:
                res.append((c,"not found",0)); continue
            d = json.loads(o.stdout.strip())
            res.append((c, d["s"], d["r"]))
        except:
            res.append((c,"error",0))
    return res


def vps_docker_check():
    res = []
    for name, host, container in VPS_CONTAINERS:
        try:
            out = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=10", host,
                 "docker inspect --format={{.State.Status}} " + container],
                capture_output=True, text=True, timeout=20
            )
            status = out.stdout.strip()
            res.append((name, "running" if status == "running" else (status or "not found")))
        except Exception as e:
            res.append((name, "error: " + str(e)))
    return res

def disk_check():
    res = []
    try:
        o = subprocess.run(["df","--output=target,pcent","/"], capture_output=True, text=True, timeout=10)
        for line in o.stdout.strip().split("\n")[1:]:
            p = line.split()
            if len(p) >= 2: res.append((p[0], int(p[1].replace("%",""))))
    except:
        res.append(("/", -1))
    return res

def load():
    try:
        with open(STATE) as f: return json.load(f)
    except: return {"issues":[],"restarts":{},"tunnel_failures":{},"stall_counts":{}}

def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE,"w") as f: json.dump(st, f)

def discord(msg, color=0x00ff00):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = json.dumps({"embeds":[{"title":"MOTHER Health Check","description":msg,"color":color,"footer":{"text":"Cutter74-Linux | "+now}}]}).encode()
    req = urllib.request.Request(WEBHOOK, data=body, headers={"Content-Type":"application/json","User-Agent":"Mother-HealthCheck/1.0"}, method="POST")
    try: urllib.request.urlopen(req, timeout=10)
    except Exception as e: print("Webhook failed:", e)

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
    """VPS services are reached via SSH tunnel — DOWN may mean tunnel not up, not service down."""
    return name in ("n8n VPS", "OpenClaw VPS")

def auto_repair(name, status):
    """Try to fix common issues. Returns (fixed, message)"""
    if status in ("exited", "not found", "dead", "created"):
        try:
            out = subprocess.run(["docker","start",name], capture_output=True, text=True, timeout=30)
            if out.returncode == 0:
                time.sleep(10)
                check = subprocess.run(["docker","inspect","--format","{{.State.Status}}",name], capture_output=True, text=True, timeout=10)
                new_status = check.stdout.strip()
                if new_status == "running":
                    return (True, "Auto-restarted "+name+" successfully")
                else:
                    return (False, "Tried to restart "+name+" but status is "+new_status)
            else:
                return (False, "Restart command failed for "+name+": "+out.stderr.strip())
        except Exception as e:
            return (False, "Repair attempt failed for "+name+": "+str(e))
    return (False, None)

def setup_tunnels():
    """Open SSH tunnels to VPS services via Tailscale"""
    tunnels = []
    try:
        t1 = subprocess.Popen([
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
            "-N", "-L", "15678:127.0.0.1:5678",
            "-L", "28789:127.0.0.1:28789",
            "root@5.78.179.50"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tunnels.append(t1)
        time.sleep(2)
    except Exception as e:
        print("Tunnel setup failed:", e)
    return tunnels

def teardown_tunnels(tunnels):
    for t in tunnels:
        try: t.terminate()
        except: pass


def run():
    st = load()
    bad, ok = [], []
    tunnel_failures = st.get("tunnel_failures", {})

    for name, url, t in SERVICES:
        n, status, ms, err = ping(name, url, t)
        if status == 200:
            ok.append("**"+n+"** 200 OK ("+str(ms)+"ms)")
            # Clear consecutive failure count on success
            tunnel_failures[name] = 0
        else:
            # FIX: VPS services go through SSH tunnels which may not be up.
            # Only report DOWN after STALL_TRIGGER_COUNT consecutive failures.
            if is_tunnel_service(name):
                tunnel_failures[name] = tunnel_failures.get(name, 0) + 1
                count = tunnel_failures[name]
                if count >= STALL_TRIGGER_COUNT:
                    bad.append("**"+n+"** DOWN ("+str(count)+" consecutive failures)")
                else:
                    # Soft warning — don't alert yet
                    ok.append("**"+n+"** tunnel miss "+str(count)+"/"+str(STALL_TRIGGER_COUNT)+" (not yet alerting)")
            else:
                bad.append("**"+n+"** DOWN")

    st["tunnel_failures"] = tunnel_failures

    for name, status, restarts in docker_check():
        prev = st.get("restarts",{}).get(name,0)
        nr = restarts - prev if restarts >= prev else restarts
        if status == "running" and nr == 0:
            ok.append("**"+name+"** running (restarts: "+str(restarts)+")")
        elif status == "running":
            bad.append("**"+name+"** "+str(nr)+" new restart(s)")
        else:
            fixed, msg = auto_repair(name, status)
            if fixed:
                ok.append("**"+name+"** SELF-HEALED (was "+status+")")
                bad.append("**"+name+"** was down - auto-restarted")
            else:
                bad.append("**"+name+"** "+status)
        st.setdefault("restarts",{})[name] = restarts

    for mount, pct in disk_check():
        if pct >= 90:
            bad.append("Disk "+mount+" "+str(pct)+"% CRITICAL")
        elif pct >= 80:
            bad.append("Disk "+mount+" "+str(pct)+"% WARNING")
        elif pct >= 0:
            ok.append("Disk "+mount+" "+str(pct)+"% used")

    if bad:
        m = "**Issues:**\n"+"\n".join("x "+i for i in bad)
        if ok: m += "\n\n**Healthy:**\n"+"\n".join("v "+i for i in ok)
        discord(m, 0xff4444)
        st["issues"] = bad
        print("ALERT sent")
    else:
        if st.get("issues") or "--verbose" in sys.argv:
            m = "**All Systems Green**\n"+"\n".join("v "+i for i in ok)
            discord(m, 0x00ff00)
            st["issues"] = []
            print("ALL CLEAR sent")
        else:
            print("All clear - silent")
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
        for name, url, t in SERVICES:
            _, status, ms, _ = ping(name, url, t)
            if status == 200:
                ok.append("**"+name+"** ✅ "+str(ms)+"ms")
        for name, status, restarts in docker_check():
            if status == "running":
                ok.append("**"+name+"** ✅ running (restarts: "+str(restarts)+")")
        for mount, pct in disk_check():
            if pct >= 0:
                ok.append("Disk **"+mount+"** "+str(pct)+"%")
        msg = "💓 **Mother Heartbeat** — All systems watched\n" + "\n".join("• "+i for i in ok)
        discord(msg, 0x5865f2)
        with open(STATE_HB, "w") as f:
            json.dump({"last": now}, f)
        print("Heartbeat sent")


def check_strategy_health():
    """Check AXIS_PMS health file for strategy stalls. Triggers n8n remediation on stall."""
    from datetime import timezone
    VPS = "root@5.78.179.50"
    HEALTH_FILE = "/home/node/.openclaw/workspace/memory/scan-health-axis_pms.json"
    NOW = datetime.now(timezone.utc)

    st = load()
    stall_counts = st.get("stall_counts", {})

    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             VPS, "docker", "exec", "repo-openclaw-gateway-1", "cat", HEALTH_FILE],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 or not result.stdout.strip():
            discord("STRATEGY WATCHDOG - Could not read AXIS_PMS health file locally", 0xffa500)
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
            scan_time = datetime.strptime(scan_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
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
        # Increment consecutive stall counter
        stall_counts["axis_pms"] = stall_counts.get("axis_pms", 0) + 1
        count = stall_counts["axis_pms"]

        msg = "AXIS_PMS WATCHDOG ALERT (stall #" + str(count) + ")\n" + "\n".join(alerts)
        msg += "\n\nLast scan: " + (scan_time_str or "unknown")
        msg += "\nMarkets: " + str(markets_scanned) + " | Signals: " + str(signals_found)
        msg += " (RED:" + str(red_signals) + " YELLOW:" + str(yellow_signals) + ")"
        discord(msg, 0xff0000)

        # Trigger n8n remediation after STALL_TRIGGER_COUNT consecutive stalls
        if count >= STALL_TRIGGER_COUNT:
            trigger_remediation(
                trigger_type="strategy_stall",
                service_name="repo-openclaw-gateway-1",
                alert_type="STRATEGY_STALL",
                details={
                    "scan_status": scan_status,
                    "hours_since_scan": hours_since,
                    "markets_scanned": markets_scanned,
                    "consecutive_stalls": count
                }
            )
            # Reset counter after triggering so we don't spam
            stall_counts["axis_pms"] = 0

        print("AXIS_PMS watchdog alert sent (stall #" + str(count) + ")")
    else:
        # Clear stall counter on recovery
        if stall_counts.get("axis_pms", 0) > 0:
            print("AXIS_PMS recovered after " + str(stall_counts["axis_pms"]) + " stalls")
        stall_counts["axis_pms"] = 0
        print("AXIS_PMS health OK - " + str(markets_scanned) + " markets, " + str(signals_found) + " signals, " + str(hours_since) + "h ago")

    st["stall_counts"] = stall_counts
    save(st)


if __name__ == "__main__":
    tunnels = setup_tunnels()
    try:
        heartbeat()
        run()
        check_strategy_health()
    finally:
        teardown_tunnels(tunnels)
