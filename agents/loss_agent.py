import socket
from common import CONFIG, post_report, register_agent, pause

AGENT_ID = "agent-2-connection-loss"
AGENT_NAME = "Connection-Loss Agent"
CAPABILITIES = ["tcp_probe_failure_percent"]


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
    register_agent(AGENT_ID, AGENT_NAME, CAPABILITIES, float(CONFIG.get("interval_seconds", 5)))
    print(f"{AGENT_ID} started")
    while True:
        for target in CONFIG.get("targets", []):
            value, status, details = check(target)
            post_report(AGENT_ID, "tcp_probe_failure_percent", target["name"], value, status, details)
        pause()
