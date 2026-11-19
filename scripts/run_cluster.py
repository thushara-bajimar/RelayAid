"""Start a complete local RelayAid deployment: the central server plus three field nodes,
each as a separate OS process with its own SQLite database.

    python scripts/run_cluster.py            # provision on first run, then start
    python scripts/run_cluster.py --reset    # wipe runtime-data/ and re-provision
    python scripts/run_cluster.py --seed     # also load demo data after start-up

Press Ctrl+C to stop every node.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from relayaid.config import DEFAULT_NODES, create_deployment  # noqa: E402


def provision(runtime: Path, reset: bool) -> None:
    if reset and runtime.exists():
        shutil.rmtree(runtime)
        print(f"removed {runtime}")
    if not (runtime / "deployment.json").exists():
        create_deployment(runtime, DEFAULT_NODES)
        print(f"provisioned deployment in {runtime} (keys, roster, operator accounts)")


def wait_healthy(port: int, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=os.environ.get("RELAYAID_RUNTIME_DIR", str(ROOT / "runtime-data")))
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--no-auto-sync", action="store_true")
    args = ap.parse_args()
    runtime = Path(args.runtime).resolve()
    provision(runtime, args.reset)

    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    procs = []
    for n in DEFAULT_NODES:
        cmd = [sys.executable, "-m", "relayaid.serve", "--node", n["node_id"], "--runtime", str(runtime)]
        if args.no_auto_sync:
            cmd.append("--no-auto-sync")
        procs.append(subprocess.Popen(cmd, env=env, cwd=str(ROOT)))
    try:
        for n in DEFAULT_NODES:
            ok = wait_healthy(n["port"])
            print(f"  {n['node_id']:<8} {n['team']:<28} http://127.0.0.1:{n['port']}  {'up' if ok else 'FAILED'}")
        if args.seed:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_demo.py")], env=env, check=False)
        print("\nRelayAid is running. Start the dashboard with:  cd frontend && npm run dev")
        print("Press Ctrl+C to stop all nodes.")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT if os.name != "nt" else signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("stopped")


if __name__ == "__main__":
    main()
