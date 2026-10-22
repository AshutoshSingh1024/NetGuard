from common import CONFIG, post_report, register_agent, pause, CONTROLLER
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

AGENT_ID = "agent-5-traffic"
AGENT_NAME = "Traffic Capture Agent"
CAPABILITIES = ["traffic_summary", "packet_capture"]

SCAPY_AVAILABLE = False
try:
    from scapy.all import sniff, IP, TCP, UDP, ICMP, Raw
    SCAPY_AVAILABLE = True
except Exception:
    SCAPY_AVAILABLE = False


def _proto_name(pkt):
    if pkt.haslayer(ICMP):
        return "ICMP"
    if pkt.haslayer(TCP):
        return "TCP"
    if pkt.haslayer(UDP):
        return "UDP"
    if pkt.haslayer(IP):
        return "IP"
    return "OTHER"


def _capture(interval_seconds):
    """Capture packets for interval_seconds and return aggregate stats + samples."""
    stats = {
        "total": 0,
        "by_protocol": {},
        "bytes_by_protocol": {},
        "top_sources": {},
        "top_destinations": {},
        "samples": [],
    }

    if not SCAPY_AVAILABLE:
        stats["error"] = "scapy not installed (pip install scapy) + Npcap/WinPcap required on Windows"
        return stats

    def handler(pkt):
        try:
            if not pkt.haslayer(IP):
                return
            ip_layer = pkt.getlayer(IP)
            src = ip_layer.src
            dst = ip_layer.dst
            proto = _proto_name(pkt)
            length = int(len(pkt))

            stats["total"] += 1
            stats["by_protocol"][proto] = stats["by_protocol"].get(proto, 0) + 1
            stats["bytes_by_protocol"][proto] = stats["bytes_by_protocol"].get(proto, 0) + length
            stats["top_sources"][src] = stats["top_sources"].get(src, 0) + 1
            stats["top_destinations"][dst] = stats["top_destinations"].get(dst, 0) + 1

            sport = dport = None
            if pkt.haslayer(TCP):
                sport = pkt.sport
                dport = pkt.dport
            elif pkt.haslayer(UDP):
                sport = pkt.sport
                dport = pkt.dport

            info = ""
            if pkt.haslayer(Raw):
                raw = pkt[Raw].load[:40]
                try:
                    text = raw.decode("utf-8")
                    if all(32 <= b < 127 or b in (9, 10, 13) for b in text.encode("utf-8")):
                        info = text.replace("\n", " ").replace("\r", "")
                    else:
                        info = "encrypted"
                except UnicodeDecodeError:
                    info = "encrypted"

            if len(stats["samples"]) < 200:
                stats["samples"].append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "src_ip": src,
                    "dst_ip": dst,
                    "src_port": sport,
                    "dst_port": dport,
                    "protocol": proto,
                    "length": length,
                    "info": info,
                })
        except Exception:
            pass

    sniff(prn=handler, store=False, timeout=max(1, int(interval_seconds)))
    return stats


def _post_packets(samples):
    if not samples:
        return
    try:
        data = json.dumps({"packets": samples}).encode("utf-8")
        req = urllib.request.Request(
            CONTROLLER + "/api/packets/batch",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as exc:
        print(f"[traffic] could not send packet batch: {exc}")


if __name__ == "__main__":
    interval = float(CONFIG.get("interval_seconds", 5))
    register_agent(AGENT_ID, AGENT_NAME, CAPABILITIES, interval)
    print(f"{AGENT_ID} started (scapy={'yes' if SCAPY_AVAILABLE else 'no'})")
    while True:
        stats = _capture(interval)

        # Report summary metrics
        post_report(AGENT_ID, "traffic_total_packets", "capture-interface",
                    stats["total"], "NORMAL",
                    f"protocols={json.dumps(stats['by_protocol'])}")
        post_report(AGENT_ID, "traffic_protocols", "capture-interface",
                    len(stats["by_protocol"]), "NORMAL",
                    json.dumps(stats["bytes_by_protocol"]))

        # Top source talkers
        top_src = sorted(stats["top_sources"].items(), key=lambda x: x[1], reverse=True)[:5]
        for ip, cnt in top_src:
            post_report(AGENT_ID, "traffic_top_source", ip, cnt, "NORMAL",
                        f"{cnt} packets")

        # Send packet samples to controller
        _post_packets(stats.get("samples", []))

        if stats.get("error"):
            print(f"[traffic] {stats['error']}")

        pause()
