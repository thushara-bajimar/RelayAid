"""Network condition simulator.

All nodes run on one laptop, so real radio links are emulated per node. The simulator is
enforced on *both* ends of every peer connection:

* the initiator refuses to send if it is offline or the link to that peer is cut
  (no packet is ever produced), and
* the receiver answers 503 `link_down` if it is offline or has cut the sender.

Settings persist in the node's own database, so partitions survive restarts.
"""
from __future__ import annotations

import json
import random
import time

DEFAULTS = {
    "offline": False,          # node has no connectivity at all
    "blocked_peers": [],       # peers this node cannot reach (partition)
    "latency_ms": 0,           # added delay per request (weak / delayed link)
    "drop_rate": 0.0,          # probability a request is lost (triggers retry/backoff)
    "batch_size": 100,         # events per sync batch (bounded payloads)
    "window_batches": 0,       # max batches per session; 0 = unlimited (short sync windows)
    "duplicate_delivery": False,  # deliver every batch twice (idempotency demo)
    "shuffle": False,          # shuffle event order inside batches (order-independence demo)
    "auto_sync": True,         # background gossip with reachable peers
    "auto_sync_interval_s": 5,
}


class LinkDown(Exception):
    pass


class SimulatedDrop(Exception):
    pass


class NetSim:
    def __init__(self, db, rng: random.Random | None = None):
        self.db = db
        self.rng = rng or random.Random()
        self._cache = None

    def get(self) -> dict:
        if self._cache is None:
            state = dict(DEFAULTS)
            for row in self.db.query("SELECT key, value FROM sim_state"):
                if row["key"] in DEFAULTS:
                    state[row["key"]] = json.loads(row["value"])
            self._cache = state
        return dict(self._cache)

    def update(self, **changes) -> dict:
        state = self.get()
        for key, value in changes.items():
            if key not in DEFAULTS or value is None:
                continue
            if key == "blocked_peers":
                value = sorted(set(value))
            if key == "drop_rate":
                value = max(0.0, min(float(value), 0.95))
            if key in ("batch_size",):
                value = max(1, min(int(value), 1000))
            state[key] = value
            self.db.execute("INSERT INTO sim_state (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))
        self._cache = state
        return dict(state)

    def reset(self) -> dict:
        self.db.execute("DELETE FROM sim_state")
        self._cache = None
        return self.get()

    def link_up(self, peer_id: str) -> bool:
        s = self.get()
        return not s["offline"] and peer_id not in s["blocked_peers"]

    def check_link(self, peer_id: str) -> None:
        if not self.link_up(peer_id):
            raise LinkDown(f"link to {peer_id} is down")

    def before_request(self) -> None:
        s = self.get()
        if s["latency_ms"]:
            time.sleep(s["latency_ms"] / 1000)
        if s["drop_rate"] and self.rng.random() < s["drop_rate"]:
            raise SimulatedDrop("simulated packet loss")
