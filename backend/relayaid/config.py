"""Deployment provisioning and per-node configuration.

A *deployment* is a set of nodes that trust each other. Provisioning ("pairing") creates:

* one Ed25519 identity per node (private key stays in that node's directory only),
* a roster of node ids, roles, URLs and public keys shared by every node,
* one deployment data key (DEK) for sensitive person fields, stored only *wrapped* under
  each operator's passphrase. The central server ("hub") never receives the DEK, so it
  relays sensitive records as ciphertext it cannot read.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import crypto

DEFAULT_PASSPHRASE = "relayaid-demo"  # local demo only; override with RELAYAID_OPERATOR_PASSPHRASE

DEFAULT_NODES = [
    {"node_id": "hub", "team": "Central Coordination Server", "role": "server", "port": 8000},
    {"node_id": "alpha", "team": "Rescue Team North", "role": "node", "port": 8001},
    {"node_id": "bravo", "team": "Rescue Team South", "role": "node", "port": 8002},
    {"node_id": "charlie", "team": "Relief Coordination Team", "role": "node", "port": 8003},
]

# Operator accounts created on every field node. The server gets no people-capable account.
OPERATOR_ROLES = {
    "coordinator": {"people_details": True, "lab": True, "write": True, "resolve": True},
    "responder": {"people_details": False, "lab": False, "write": True, "resolve": False},
    "viewer": {"people_details": False, "lab": False, "write": False, "resolve": False},
}


@dataclass
class PeerInfo:
    node_id: str
    team: str
    role: str
    url: str
    public_key: str


@dataclass
class NodeConfig:
    node_id: str
    team: str
    role: str  # "node" (field device) or "server" (optional central hub)
    url: str
    port: int
    data_dir: Path
    signing_key: str
    roster: dict[str, PeerInfo]
    operators: dict[str, dict]
    session_minutes: int = 60
    deployment_id: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / f"{self.node_id}.db"

    @property
    def is_server(self) -> bool:
        return self.role == "server"

    def peers(self) -> list[PeerInfo]:
        return [p for nid, p in sorted(self.roster.items()) if nid != self.node_id]


def create_deployment(root: Path | str, nodes: list[dict] | None = None, passphrase: str | None = None,
                      host: str = "127.0.0.1") -> dict[str, NodeConfig]:
    """Provision a fresh deployment under `root`. Returns configs keyed by node id."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    nodes = nodes or DEFAULT_NODES
    passphrase = passphrase or os.environ.get("RELAYAID_OPERATOR_PASSPHRASE", DEFAULT_PASSPHRASE)
    dek = crypto.generate_data_key()
    deployment_id = secrets.token_hex(8)

    keys: dict[str, tuple[str, str]] = {n["node_id"]: crypto.generate_signing_key() for n in nodes}
    roster = [
        {"node_id": n["node_id"], "team": n["team"], "role": n["role"],
         "url": n.get("url") or f"http://{host}:{n['port']}", "public_key": keys[n["node_id"]][1]}
        for n in nodes
    ]
    (root / "deployment.json").write_text(json.dumps({
        "deployment_id": deployment_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "roster": roster,
    }, indent=2))

    for n in nodes:
        nd = root / n["node_id"]
        nd.mkdir(parents=True, exist_ok=True)
        (nd / "identity.key").write_text(keys[n["node_id"]][0])
        try:
            os.chmod(nd / "identity.key", 0o600)
        except OSError:
            pass
        operators = {}
        if n["role"] != "server":
            for username, perms in OPERATOR_ROLES.items():
                entry = {"role": username, "passphrase_hash": crypto.hash_passphrase(passphrase)}
                if perms["write"]:  # writers need the DEK to encrypt new person records
                    entry["wrapped_dek"] = crypto.wrap_key(passphrase, dek)
                operators[username] = entry
        (nd / "operators.json").write_text(json.dumps(operators, indent=2))
        (nd / "node.json").write_text(json.dumps({
            "node_id": n["node_id"], "team": n["team"], "role": n["role"], "port": n["port"],
        }, indent=2))
    return {n["node_id"]: load_node_config(root, n["node_id"]) for n in nodes}


def load_node_config(root: Path | str, node_id: str) -> NodeConfig:
    root = Path(root)
    dep = json.loads((root / "deployment.json").read_text())
    roster = {r["node_id"]: PeerInfo(**r) for r in dep["roster"]}
    nd = root / node_id
    meta = json.loads((nd / "node.json").read_text())
    return NodeConfig(
        node_id=node_id,
        team=meta["team"],
        role=meta["role"],
        url=roster[node_id].url,
        port=meta["port"],
        data_dir=nd,
        signing_key=(nd / "identity.key").read_text().strip(),
        roster=roster,
        operators=json.loads((nd / "operators.json").read_text()),
        session_minutes=int(os.environ.get("RELAYAID_SESSION_MINUTES", "60")),
        deployment_id=dep["deployment_id"],
    )
