import socket
import time
from common import CONFIG, post_report, pause

AGENT_ID = "agent-1-latency"


def check(target):
    start = time.perf_counter()
    try:
        with socket.create_connection(
            (target["host"], int(target["port"])),
            timeout=float(CONFIG.get("connect_timeout_seconds", 2))
        ):
            elapsed_ms = (time.perf_counter() - start) * 1000
        status = "HIGH" if elapsed_ms > 150 else "NORMAL"
        return round(elapsed_ms, 2), status, "TCP connection time in milliseconds"
    except OSError as exc:
        return None, "UNREACHABLE", str(exc)


if __name__ == "__main__":
    print(f"{AGENT_ID} started")
    while True:
        for target in CONFIG["targets"]:
            value, status, details = check(target)
            post_report(AGENT_ID, "tcp_latency_ms", target["name"], value, status, details)
        pause()
