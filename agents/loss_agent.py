import socket
from common import CONFIG, post_report, pause

AGENT_ID = "agent-2-connection-loss"


def check(target, attempts=5):
    failed = 0
    errors = []
    timeout = float(CONFIG.get("connect_timeout_seconds", 2))
    for _ in range(attempts):
        try:
            with socket.create_connection((target["host"], int(target["port"])), timeout=timeout):
                pass
        except OSError as exc:
            failed += 1
            if len(errors) < 2:
                errors.append(str(exc))
    percent = round(100 * failed / attempts, 1)
    status = "HIGH" if percent >= 20 else "NORMAL"
    details = f"{failed}/{attempts} TCP connection attempts failed"
    if errors:
        details += "; " + " | ".join(errors)
    return percent, status, details


if __name__ == "__main__":
    print(f"{AGENT_ID} started")
    while True:
        for target in CONFIG["targets"]:
            value, status, details = check(target)
            post_report(AGENT_ID, "tcp_probe_failure_percent", target["name"], value, status, details)
        pause()
