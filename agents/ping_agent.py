from common import CONFIG, post_report, register_agent, pause, ping_host

AGENT_ID = "agent-4-ping"
AGENT_NAME = "Ping Agent"
CAPABILITIES = ["ping_rtt_ms", "ping_loss_percent"]


if __name__ == "__main__":
    register_agent(AGENT_ID, AGENT_NAME, CAPABILITIES, float(CONFIG.get("interval_seconds", 5)))
    print(f"{AGENT_ID} started")
    while True:
        for target in CONFIG.get("targets", []):
            host = target["host"] if isinstance(target, dict) else target
            name = target.get("name", host) if isinstance(target, dict) else host
            res = ping_host(host)
            post_report(AGENT_ID, "ping_rtt_ms", name, res["rtt_ms"], res["status"], res["details"])
            post_report(AGENT_ID, "ping_loss_percent", name, res["loss_pct"], res["status"], res["details"])
        pause()
