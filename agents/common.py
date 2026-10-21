import json
import time
import platform
import re
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG_PATH = ROOT / "config.json"

try:
    with open(LOCAL_CONFIG_PATH, encoding="utf-8") as f:
        LOCAL_CONFIG = json.load(f)
except FileNotFoundError:
    LOCAL_CONFIG = {}


def _fetch_remote_config(controller_url):
    try:
        req = urllib.request.Request(controller_url + "/api/config", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def load_config(controller_url=None):
    """Prefer centralized config from controller; fall back to local config.json."""
    if controller_url:
        remote = _fetch_remote_config(controller_url)
        if remote:
            return remote
    return LOCAL_CONFIG


CONTROLLER = LOCAL_CONFIG.get("controller_url", "http://127.0.0.1:5000")
CONFIG = load_config(CONTROLLER)


def post_report(agent_id, metric, target, value, status, details=""):
    payload = {
        "agent_id": agent_id,
        "metric": metric,
        "target": target,
        "value": value,
        "status": status,
        "details": details,
    }
    return _http_post(CONTROLLER + "/api/report", payload)


def register_agent(agent_id, name, capabilities, interval):
    payload = {
        "agent_id": agent_id,
        "name": name,
        "capabilities": capabilities,
        "interval": interval,
    }
    return _http_post(CONTROLLER + "/api/register", payload)


def _http_post(url, payload, timeout=5):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"could not reach controller: {exc}")
        return None


def pause():
    time.sleep(max(1, float(CONFIG.get("interval_seconds", 5))))


def _parse_windows(output):
    rtt = None
    sent = received = lost = None
    m = re.search(r"time[=< ](\d+(?:\.\d+)?)\s*ms", output)
    if m:
        rtt = float(m.group(1))
    m = re.search(r"Sent\s*=\s*(\d+)", output)
    if m:
        sent = int(m.group(1))
    m = re.search(r"Received\s*=\s*(\d+)", output)
    if m:
        received = int(m.group(1))
    m = re.search(r"Lost\s*=\s*(\d+)", output)
    if m:
        lost = int(m.group(1))
    return rtt, sent, received, lost


def _parse_linux(output):
    rtt = None
    m = re.search(r"time[=< ](\d+(?:\.\d+)?)\s*ms", output)
    if m:
        rtt = float(m.group(1))
    m = re.search(r"(\d+)% packet loss", output)
    loss_pct = float(m.group(1)) if m else None
    sent = received = None
    m = re.search(r"(\d+) packets transmitted, (\d+) received", output)
    if m:
        sent = int(m.group(1))
        received = int(m.group(2))
    lost = (sent - received) if (sent is not None and received is not None) else None
    return rtt, sent, received, lost, loss_pct


def ping_host(host, count=1, timeout_ms=2000):
    """Ping a host once. Returns dict with rtt_ms, status, loss_pct, details."""
    system = platform.system().lower()
    try:
        if system == "windows":
            cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=max(3, timeout_ms / 1000 + 2),
                creationflags=creationflags,
            )
            output = proc.stdout + proc.stderr
            rtt, sent, received, lost = _parse_windows(output)
            loss_pct = round(100 * lost / sent, 1) if (sent and sent > 0) else None
        else:
            cmd = ["ping", "-c", str(count), "-W", str(max(1, timeout_ms // 1000)), host]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=max(4, timeout_ms / 1000 + 2)
            )
            output = proc.stdout + proc.stderr
            rtt, sent, received, lost, loss_pct = _parse_linux(output)

        if proc.returncode != 0 or rtt is None:
            return {
                "rtt_ms": None,
                "status": "UNREACHABLE",
                "loss_pct": loss_pct if loss_pct is not None else 100.0,
                "details": (output.strip().replace("\n", " ") or "no response")[:300],
            }

        status = "HIGH" if rtt and rtt > 150 else "NORMAL"
        details = f"{received}/{sent} replied" if sent is not None else "replied"
        return {
            "rtt_ms": round(rtt, 2),
            "status": status,
            "loss_pct": loss_pct if loss_pct is not None else 0.0,
            "details": details,
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "rtt_ms": None,
            "status": "UNREACHABLE",
            "loss_pct": 100.0,
            "details": str(exc),
        }


def traceroute_host(host, max_hops=15, timeout_ms=2000):
    system = platform.system().lower()
    try:
        if system == "windows":
            cmd = ["tracert", "-d", "-h", str(max_hops), "-w", str(timeout_ms), host]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=creationflags)
            output = proc.stdout + proc.stderr
        else:
            cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", str(max(1, timeout_ms // 1000)), host]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            output = proc.stdout + proc.stderr
        return {
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "output": output[:2000],
            "status": "OK" if proc.returncode == 0 else "ERROR",
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"command": "", "returncode": -1, "output": str(exc), "status": "ERROR"}


def resolve_dns(host):
    import socket
    try:
        ip = socket.gethostbyname(host)
        return {"host": host, "ip": ip, "status": "OK"}
    except OSError as exc:
        return {"host": host, "ip": None, "status": "ERROR", "details": str(exc)}


def check_port(host, port, timeout_ms=2000):
    import socket
    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=max(0.5, timeout_ms / 1000)):
            elapsed_ms = (time.perf_counter() - start) * 1000
        return {"host": host, "port": int(port), "rtt_ms": round(elapsed_ms, 2), "status": "OPEN"}
    except OSError as exc:
        return {"host": host, "port": int(port), "rtt_ms": None, "status": "CLOSED", "details": str(exc)}


def check_http(url, timeout_ms=5000):
    try:
        req = urllib.request.Request(url, method="GET")
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=max(1, timeout_ms / 1000)) as response:
            elapsed_ms = (time.perf_counter() - start) * 1000
            body = response.read(200)
        return {
            "url": url,
            "status_code": response.status,
            "rtt_ms": round(elapsed_ms, 2),
            "status": "OK",
            "details": body[:100].decode("utf-8", "replace"),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"url": url, "status_code": None, "rtt_ms": None, "status": "ERROR", "details": str(exc)}
