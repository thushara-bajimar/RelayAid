"""Run one RelayAid node as its own process:  python -m relayaid.serve --node alpha"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_node_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a RelayAid node")
    parser.add_argument("--node", required=True, help="node id, e.g. alpha")
    parser.add_argument("--runtime", default=os.environ.get("RELAYAID_RUNTIME_DIR", "runtime-data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-auto-sync", action="store_true", help="disable the background gossip thread")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{args.node}] %(name)s %(message)s")
    cfg = load_node_config(Path(args.runtime), args.node)
    app = create_app(cfg, background_sync=not args.no_auto_sync)
    uvicorn.run(app, host=args.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
