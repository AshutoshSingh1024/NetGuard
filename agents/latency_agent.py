import socket
import time
from common import CONFIG, post_report, register_agent, pause

AGENT_ID = "agent-1-latency"
AGENT_NAME = "Latency Agent"
CAPABILITIES = ["tcp_latency_ms"]


def check(target):
    start = time.perf_counter()
    try:
        with socket.create_connection(
            (target["host"], int(target["port"])),
            timeout=float(CONFIG.get("connect_timeout_seconds", 2)),
        ):
            elapsed_ms = (time.perf_counter() - start) * 1000
        status = "HIGH" if elapsed_ms > 150 else "NORMAL"
        return round(elapsed_ms, 2), status, "TCP connection time in milliseconds"
    except OSError as exc:
        return None, "UNREACHABLE", str(exc)


if __name__ == "__main__":
    register_agent(AGENT_ID, AGENT_NAME, CAPABILITIES, float(CONFIG.get("interval_seconds", 5)))
    print(f"{AGENT_ID} started")
    while True:
        for target in CONFIG.get("targets", []):
            value, status, details = check(target)
            post_report(AGENT_ID, "tcp_latency_ms", target["name"], value, status, details)
        pause()
