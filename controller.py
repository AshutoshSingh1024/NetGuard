import json
import queue
import threading
import time
import urllib.request as urllib_request
from datetime import datetime, timezone
from threading import Lock

from flask import Flask, jsonify, request, send_from_directory, Response

import db
from agents.common import ping_host, traceroute_host, resolve_dns, check_port, check_http

app = Flask(__name__, static_folder="dashboard")

with open("config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

BIND_HOST = CONFIG.get("bind_host", "0.0.0.0")
BIND_PORT = int(CONFIG.get("bind_port", 5000))
OFFLINE_THRESHOLD_SECONDS = float(CONFIG.get("offline_threshold_seconds", 30))

db.init_db()

lock = Lock()
sse_subscribers = []


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _broadcast(record):
    dead = []
    with lock:
        for q in sse_subscribers:
            try:
                q.put_nowait(record)
            except queue.Full:
                dead.append(q)
    for q in dead:
        try:
            sse_subscribers.remove(q)
        except ValueError:
            pass


def _check_thresholds(record):
    """Evaluate metric against thresholds; fire alert with cooldown."""
    thresholds = db.get_thresholds()
    metric = record["metric"]
    target = record["target"]
    value = record["value"]
    if value is None:
        return

    now = time.time()
    for th in thresholds:
        if th["metric"] != metric:
            continue
        # Simple target match: exact or contains
        if th["target_pattern"] and th["target_pattern"] not in target and th["target_pattern"] != target:
            continue
        invert = th.get("invert") in (True, "true", 1, "1")
        warning = th["warning"]
        critical = th["critical"]

        status = record["status"]
        level = None
        threshold_val = None
        if not invert:
            if critical is not None and value >= critical:
                level = "CRITICAL"
                threshold_val = critical
            elif warning is not None and value >= warning:
                level = "WARNING"
                threshold_val = warning
        else:
            if critical is not None and value <= critical:
                level = "CRITICAL"
                threshold_val = critical
            elif warning is not None and value <= warning:
                level = "WARNING"
                threshold_val = warning

        if not level:
            continue

        # Cooldown: check last alert for same metric+target+level
        with lock:
            conn = db.get_conn()
            try:
                row = conn.execute(
                    """SELECT timestamp FROM alerts
                       WHERE agent_id = ? AND metric = ? AND target = ? AND details LIKE ?
                       ORDER BY id DESC LIMIT 1""",
                    (record["agent_id"], metric, target, f"%{level}%"),
                ).fetchone()
            finally:
                conn.close()

        cooldown = int(th.get("cooldown_seconds", 300))
        if row:
            try:
                last = datetime.fromisoformat(row["timestamp"]).timestamp()
                if now - last < cooldown:
                    continue
            except Exception:
                pass

        alert_record = {
            "agent_id": record["agent_id"],
            "metric": metric,
            "target": target,
            "value": value,
            "status": status,
            "details": f"{level} threshold breached (value={value}, threshold={threshold_val}) · {record['details']}",
            "timestamp": utc_now(),
        }
        insert_alert_and_notify(alert_record)


def insert_alert_and_notify(record):
    db.insert_alert(record)
    _notify(record)
    _broadcast(record)


def _notify(record):
    """Pluggable notification sinks: webhook, console, smtp stub."""
    notif = CONFIG.get("notifications", {})
    webhook_url = notif.get("webhook_url")
    payload = {
        "agent_id": record["agent_id"],
        "metric": record["metric"],
        "target": record["target"],
        "value": record["value"],
        "status": record["status"],
        "details": record["details"],
        "timestamp": record["timestamp"],
    }
    if webhook_url:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib_request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib_request.urlopen(req, timeout=5) as resp:
                resp.read()
        except Exception as exc:
            print(f"[notify] webhook failed: {exc}")
    print(f"[ALERT] {record['agent_id']} {record['metric']} {record['target']} {record['status']} · {record['details']}")


def _ingest(record):
    db.insert_metric(record)
    db.agent_heartbeat(record["agent_id"])
    _check_thresholds(record)
    _broadcast(record)
    return record


@app.get("/")
def index():
    return send_from_directory("dashboard", "index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "time": utc_now()})


@app.get("/api/config")
def api_config():
    return jsonify(CONFIG)


@app.get("/api/agents")
def api_agents():
    db.mark_offline_agents(OFFLINE_THRESHOLD_SECONDS)
    agents = db.get_agents()
    return jsonify({"agents": agents})


@app.post("/api/register")
def api_register():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    required = {"agent_id", "name", "capabilities", "interval"}
    if not required.issubset(payload):
        return jsonify({"error": "Missing fields", "required": sorted(required)}), 400
    db.upsert_agent(payload["agent_id"], payload["name"], json.dumps(payload["capabilities"]))
    return jsonify({"registered": True, "agent_id": payload["agent_id"]}), 202


@app.get("/api/metrics")
def api_metrics():
    latest = db.get_latest_metrics()
    history = db.get_recent_history(limit=200)
    agents = db.get_agents()
    return jsonify({
        "latest": {f"{r['agent_id']}|{r['metric']}|{r['target']}": r for r in latest},
        "history": history,
        "agents": agents,
    })


@app.get("/api/history")
def api_history():
    limit = int(request.args.get("limit", 200))
    agent_id = request.args.get("agent_id")
    metric = request.args.get("metric")
    history = db.get_recent_history(limit=limit, agent_id=agent_id, metric=metric)
    return jsonify({"history": history})


@app.get("/api/alerts")
def api_alerts():
    acknowledged = request.args.get("acknowledged")
    acknowledged = None if acknowledged is None else (acknowledged.lower() == "true")
    alerts = db.get_recent_alerts(limit=100, acknowledged=acknowledged)
    return jsonify({"alerts": alerts})


@app.post("/api/alerts/<int:alert_id>/ack")
def api_ack_alert(alert_id):
    db.acknowledge_alert(alert_id)
    _broadcast({"type": "alert_acked", "alert_id": alert_id})
    return jsonify({"acknowledged": True})


@app.post("/api/report")
def report():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    required = {"agent_id", "metric", "target", "value", "status"}
    if not required.issubset(payload):
        return jsonify({"error": "Missing fields", "required": sorted(required)}), 400
    if not isinstance(payload["agent_id"], str) or not isinstance(payload["metric"], str):
        return jsonify({"error": "agent_id and metric must be strings"}), 400

    record = {
        "agent_id": payload["agent_id"][:80],
        "metric": payload["metric"][:80],
        "target": str(payload["target"])[:200],
        "value": payload["value"],
        "status": str(payload["status"])[:80],
        "details": str(payload.get("details", ""))[:500],
        "timestamp": utc_now(),
    }
    _ingest(record)
    return jsonify({"accepted": True, "record": record}), 202


@app.post("/api/ping")
def api_ping():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("host"), str):
        return jsonify({"error": 'Expected JSON object with a "host" string'}), 400
    host = payload["host"].strip()
    if not host:
        return jsonify({"error": "host must not be empty"}), 400
    res = ping_host(host)
    record = {
        "agent_id": "manual-ping",
        "metric": "ping_rtt_ms",
        "target": host,
        "value": res["rtt_ms"],
        "status": res["status"],
        "details": f"loss {res['loss_pct']}% · {res['details']}",
        "timestamp": utc_now(),
    }
    _ingest(record)
    return jsonify({
        "host": host,
        "rtt_ms": res["rtt_ms"],
        "status": res["status"],
        "loss_pct": res["loss_pct"],
        "details": res["details"],
        "timestamp": record["timestamp"],
    }), 200


@app.post("/api/check")
def api_check():
    """Ad-hoc checks: ping, traceroute, dns, port, http."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    check_type = payload.get("type")
    if check_type == "ping":
        res = ping_host(payload["host"])
    elif check_type == "traceroute":
        res = traceroute_host(payload.get("host", ""))
    elif check_type == "dns":
        res = resolve_dns(payload.get("host", ""))
    elif check_type == "port":
        res = check_port(payload.get("host", ""), payload.get("port", 80))
    elif check_type == "http":
        res = check_http(payload.get("url", ""))
    else:
        return jsonify({"error": 'Unknown type. Use: ping, traceroute, dns, port, http'}), 400

    record = {
        "agent_id": "manual-check",
        "metric": f"adhoc_{check_type}",
        "target": payload.get("host") or payload.get("url", "manual"),
        "value": res.get("rtt_ms") or res.get("status_code"),
        "status": res.get("status", "UNKNOWN"),
        "details": str(res.get("details", res.get("output", "")))[:500],
        "timestamp": utc_now(),
    }
    _ingest(record)
    return jsonify({"check": check_type, "result": res, "record": record}), 200


SEVERITY_ORDER = {
    "CRITICAL": 0, "UNREACHABLE": 1, "ERROR": 2, "HIGH": 3,
    "WARNING": 4, "LOW": 5, "NORMAL": 6, "UP": 7, "OK": 8, "OPEN": 9,
}


def _severity_rank(status):
    return SEVERITY_ORDER.get(str(status).upper(), 99)


@app.get("/api/critical")
def api_critical():
    """Top critical issues across all agents/targets (latest status per metric+target)."""
    latest = db.get_latest_metrics()
    issues = []
    for r in latest:
        status = str(r.get("status") or "").upper()
        if status in ("NORMAL", "UP", "OK", "OPEN"):
            continue
        issues.append({
            "agent_id": r["agent_id"],
            "metric": r["metric"],
            "target": r["target"],
            "value": r["value"],
            "status": r["status"],
            "details": r["details"],
            "timestamp": r["timestamp"],
            "rank": _severity_rank(r["status"]),
        })
    issues.sort(key=lambda x: (x["rank"], x["timestamp"]), reverse=False)
    limit = int(request.args.get("limit", 10))
    return jsonify({"critical": issues[:limit], "count": len(issues)})


@app.get("/api/traffic")
def api_traffic():
    return jsonify(db.get_traffic_stats())


@app.get("/api/packets")
def api_packets():
    limit = int(request.args.get("limit", 200))
    return jsonify({"packets": db.get_packet_summary(limit=limit)})


@app.post("/api/packets/batch")
def api_packets_batch():
    """Receive a batch of captured packets from a traffic agent."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("packets"), list):
        return jsonify({"error": 'Expected JSON object with a "packets" list'}), 400
    count = 0
    for pkt in payload["packets"]:
        pkt["timestamp"] = pkt.get("timestamp") or utc_now()
        db.insert_packet(pkt)
        count += 1
    _broadcast({
        "agent_id": "traffic-agent",
        "metric": "traffic_batch",
        "target": "capture-interface",
        "value": count,
        "status": "NORMAL",
        "details": f"ingested {count} packets",
        "timestamp": utc_now(),
    })
    return jsonify({"ingested": count}), 202


@app.get("/api/stream")
def api_stream():
    def generate():
        q = queue.Queue(maxsize=64)
        with lock:
            sse_subscribers.append(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    record = q.get(timeout=25)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(record)}\n\n"
        finally:
            with lock:
                try:
                    sse_subscribers.remove(q)
                except ValueError:
                    pass
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/bandwidth-test")
def bandwidth_test():
    try:
        size = int(request.args.get("bytes", CONFIG.get("bandwidth_bytes", 2_000_000)))
    except ValueError:
        return jsonify({"error": "bytes must be an integer"}), 400
    size = max(1024, min(size, 5_000_000))
    chunk = b"n" * 65536

    def generate():
        remaining = size
        while remaining:
            part = chunk[:min(remaining, len(chunk))]
            yield part
            remaining -= len(part)
    return Response(generate(), mimetype="application/octet-stream",
                    headers={"Content-Length": str(size), "Cache-Control": "no-store"})


def _offline_watcher():
    while True:
        time.sleep(5)
        db.mark_offline_agents(OFFLINE_THRESHOLD_SECONDS)


if __name__ == "__main__":
    watcher = threading.Thread(target=_offline_watcher, daemon=True)
    watcher.start()
    print(f"Controller + dashboard: http://{BIND_HOST}:{BIND_PORT}")
    print("Start agents: python run_agents.py")
    app.run(host=BIND_HOST, port=BIND_PORT, threaded=True, debug=False)
