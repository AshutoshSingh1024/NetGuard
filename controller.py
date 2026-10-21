from collections import deque
from datetime import datetime, timezone
from threading import Lock
import json
import time
from flask import Flask, jsonify, request, send_from_directory, Response

app = Flask(__name__, static_folder="dashboard")
lock = Lock()
history = deque(maxlen=300)
latest_by_agent = {}
alerts = deque(maxlen=100)

with open("config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/")
def index():
    return send_from_directory("dashboard", "index.html")


@app.get("/api/metrics")
def api_metrics():
    with lock:
        return jsonify({
            "latest": latest_by_agent,
            "history": list(history),
            "alerts": list(alerts)
        })


@app.get("/health")
def health():
    return jsonify({"status": "ok", "time": utc_now()})


@app.post("/api/report")
def report():
    # Prototype shared-secret-free endpoint; localhost-only listener is the main boundary.
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
        "timestamp": utc_now()
    }

    with lock:
        history.append(record)
        latest_by_agent[record["agent_id"]] = record
        if record["status"] not in ("NORMAL", "UP", "OK"):
            alerts.appendleft({
                "timestamp": record["timestamp"],
                "agent_id": record["agent_id"],
                "message": f'{record["metric"]} status: {record["status"]}',
                "details": record["details"]
            })

    return jsonify({"accepted": True, "record": record}), 202


@app.get("/bandwidth-test")
def bandwidth_test():
    # A bounded payload for the local bandwidth agent.
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


if __name__ == "__main__":
    print("Controller + dashboard: http://127.0.0.1:5000")
    print("Start agents in another terminal: python run_agents.py")
    app.run(host="127.0.0.1", port=5000, threaded=True, debug=False)
