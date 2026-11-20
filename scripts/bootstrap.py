"""Provision a RelayAid deployment (keys, roster, operator accounts) without starting nodes.

    python scripts/bootstrap.py                    # local: nodes on 127.0.0.1:7400-7403
    python scripts/bootstrap.py --docker           # compose: nodes reach each other by service name
    python scripts/bootstrap.py --runtime DIR --force
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from relayaid.config import DEFAULT_NODES, create_deployment  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=str(ROOT / "runtime-data"))
    ap.add_argument("--docker", action="store_true", help="use http://<node_id>:<port> URLs")
    ap.add_argument("--force", action="store_true", help="replace an existing deployment")
    a = ap.parse_args()
    runtime = Path(a.runtime)
    if (runtime / "deployment.json").exists():
        if not a.force:
            print(f"deployment already exists in {runtime}; use --force to replace it")
            return
        shutil.rmtree(runtime)
    nodes = [dict(n) for n in DEFAULT_NODES]
    if a.docker:
        for n in nodes:
            n["url"] = f"http://{n['node_id']}:{n['port']}"
    create_deployment(runtime, nodes)
    print(f"provisioned {len(nodes)} nodes in {runtime}")


if __name__ == "__main__":
    main()
