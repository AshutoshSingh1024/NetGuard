import time
import urllib.request
import urllib.error
from common import CONFIG, post_report, register_agent, pause, CONTROLLER

AGENT_ID = "agent-3-bandwidth"
AGENT_NAME = "Bandwidth Agent"
CAPABILITIES = ["loopback_throughput_mbps"]


def measure():
    size = int(CONFIG.get("bandwidth_bytes", 2_000_000))
    url = f"{CONTROLLER}/bandwidth-test?bytes={size}"
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            received = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                received += len(chunk)
        elapsed = max(time.perf_counter() - start, 0.000001)
        mbps = (received * 8) / elapsed / 1_000_000
        status = "LOW" if mbps < 20 else "NORMAL"
        return round(mbps, 2), status, f"Downloaded {received} bytes in {elapsed:.3f}s; local loopback test"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, "ERROR", str(exc)


if __name__ == "__main__":
    register_agent(AGENT_ID, AGENT_NAME, CAPABILITIES, float(CONFIG.get("interval_seconds", 5)))
    print(f"{AGENT_ID} started")
    while True:
        value, status, details = measure()
        post_report(AGENT_ID, "loopback_throughput_mbps", "laptop-local-test", value, status, details)
        pause()
