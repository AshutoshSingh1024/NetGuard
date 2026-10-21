import sqlite3
import json
import os
from threading import Lock

DB_PATH = os.environ.get("NETWORK_MONITOR_DB", "network_monitor.db")
_lock = Lock()


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT,
            capabilities TEXT,
            last_seen TEXT,
            registered_at TEXT,
            status TEXT DEFAULT 'online'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            metric TEXT,
            target TEXT,
            value REAL,
            status TEXT,
            details TEXT,
            timestamp TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            metric TEXT,
            target TEXT,
            value REAL,
            status TEXT,
            details TEXT,
            timestamp TEXT,
            acknowledged INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS thresholds (
            metric TEXT,
            target_pattern TEXT,
            warning REAL,
            critical REAL,
            cooldown_seconds INTEGER DEFAULT 300,
            PRIMARY KEY (metric, target_pattern)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS packets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            src_ip TEXT,
            dst_ip TEXT,
            src_port INTEGER,
            dst_port INTEGER,
            protocol TEXT,
            length INTEGER,
            info TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_packets_time ON packets(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_packets_src ON packets(src_ip)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_packets_dst ON packets(dst_ip)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_packets_proto ON packets(protocol)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_metrics_agent ON metrics(agent_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_metrics_time ON metrics(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_metrics_metric_target ON metrics(metric, target)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(timestamp)")
        conn.commit()
    finally:
        conn.close()


def upsert_agent(agent_id, name, capabilities):
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    with _lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,))
            if c.fetchone():
                c.execute(
                    "UPDATE agents SET name = ?, capabilities = ?, last_seen = ?, status = 'online' WHERE agent_id = ?",
                    (name, capabilities, now, agent_id),
                )
            else:
                c.execute(
                    "INSERT INTO agents (agent_id, name, capabilities, last_seen, registered_at, status) VALUES (?, ?, ?, ?, ?, 'online')",
                    (agent_id, name, capabilities, now, now),
                )
            conn.commit()
        finally:
            conn.close()


def agent_heartbeat(agent_id):
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE agents SET last_seen = ?, status = 'online' WHERE agent_id = ?", (now, agent_id)
            )
            conn.commit()
        finally:
            conn.close()


def insert_metric(record):
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO metrics (agent_id, metric, target, value, status, details, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["agent_id"],
                    record["metric"],
                    record["target"],
                    record["value"],
                    record["status"],
                    record["details"],
                    record["timestamp"],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def insert_alert(record):
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO alerts (agent_id, metric, target, value, status, details, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["agent_id"],
                    record["metric"],
                    record["target"],
                    record["value"],
                    record["status"],
                    record["details"],
                    record["timestamp"],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def insert_packet(pkt):
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO packets (timestamp, src_ip, dst_ip, src_port, dst_port, protocol, length, info)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pkt["timestamp"],
                    pkt.get("src_ip"),
                    pkt.get("dst_ip"),
                    pkt.get("src_port"),
                    pkt.get("dst_port"),
                    pkt.get("protocol"),
                    pkt.get("length"),
                    pkt.get("info", "")[:200],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_packet_summary(limit=5000):
    with _lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM packets ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_traffic_stats():
    with _lock:
        conn = get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM packets").fetchone()["c"]
            by_proto = conn.execute(
                "SELECT protocol, COUNT(*) as c, SUM(length) as bytes FROM packets GROUP BY protocol"
            ).fetchall()
            top_src = conn.execute(
                "SELECT src_ip, COUNT(*) as c, SUM(length) as bytes FROM packets WHERE src_ip IS NOT NULL GROUP BY src_ip ORDER BY bytes DESC LIMIT 10"
            ).fetchall()
            top_dst = conn.execute(
                "SELECT dst_ip, COUNT(*) as c, SUM(length) as bytes FROM packets WHERE dst_ip IS NOT NULL GROUP BY dst_ip ORDER BY bytes DESC LIMIT 10"
            ).fetchall()
            return {
                "total_packets": total,
                "by_protocol": [dict(r) for r in by_proto],
                "top_sources": [dict(r) for r in top_src],
                "top_destinations": [dict(r) for r in top_dst],
            }
        finally:
            conn.close()


def get_latest_metrics(limit_per_agent=1):
    with _lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT m.* FROM metrics m
                   INNER JOIN (
                       SELECT agent_id, metric, target, MAX(id) as max_id
                       FROM metrics GROUP BY agent_id, metric, target
                   ) g ON m.id = g.max_id
                   ORDER BY m.timestamp DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_recent_history(limit=200, agent_id=None, metric=None):
    with _lock:
        conn = get_conn()
        try:
            sql = "SELECT * FROM metrics"
            params = []
            where = []
            if agent_id:
                where.append("agent_id = ?")
                params.append(agent_id)
            if metric:
                where.append("metric = ?")
                params.append(metric)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_recent_alerts(limit=50, acknowledged=None):
    with _lock:
        conn = get_conn()
        try:
            sql = "SELECT * FROM alerts"
            params = []
            where = []
            if acknowledged is not None:
                where.append("acknowledged = ?")
                params.append(1 if acknowledged else 0)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_agents():
    with _lock:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def acknowledge_alert(alert_id):
    with _lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
            conn.commit()
        finally:
            conn.close()


def set_config(key, value):
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
            conn.commit()
        finally:
            conn.close()


def get_config(key, default=None):
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row["value"])
            return default
        finally:
            conn.close()


def get_thresholds():
    with _lock:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT * FROM thresholds").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def mark_offline_agents(threshold_seconds):
    import time as _time
    cutoff = _time.time() - threshold_seconds
    cutoff_iso = __import__("datetime").datetime.fromtimestamp(cutoff, __import__("datetime").timezone.utc).isoformat()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE agents SET status = 'offline' WHERE status = 'online' AND last_seen < ?", (cutoff_iso,)
            )
            conn.commit()
        finally:
            conn.close()
