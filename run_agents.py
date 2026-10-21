import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT_DIR = ROOT / "agents"
SCRIPTS = [
    AGENT_DIR / "latency_agent.py",
    AGENT_DIR / "loss_agent.py",
    AGENT_DIR / "bandwidth_agent.py",
]

processes = []
try:
    for script in SCRIPTS:
        # Use the same Python interpreter/environment as this launcher.
        p = subprocess.Popen([sys.executable, str(script)], cwd=str(AGENT_DIR))
        processes.append(p)
        print(f"Started {script.name} (PID {p.pid})")
        time.sleep(0.3)

    print("Three agents running. Press Ctrl+C to stop.")
    while True:
        for p in processes:
            if p.poll() is not None:
                print(f"Agent process {p.pid} exited with code {p.returncode}")
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopping agents...")
finally:
    for p in processes:
        if p.poll() is None:
            p.terminate()
    for p in processes:
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
    print("Agent processes stopped.")
