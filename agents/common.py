import json
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
with open(ROOT / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

CONTROLLER = "http://127.0.0.1:5000"


def post_report(agent_id, metric, target, value, status, details=""):
    payload = {
        "agent_id": agent_id,
        "metric": metric,
        "target": target,
        "value": value,
        "status": status,
        "details": details
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CONTROLLER + "/api/report",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            print(f"[{agent_id}] report accepted ({response.status}): {metric}={value} {status}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[{agent_id}] could not report to controller: {exc}")


def pause():
    time.sleep(max(1, float(CONFIG.get("interval_seconds", 5))))
