"""Test harness: a real multi-node deployment running in-process.

Each node gets its own SQLite file, identity and FastAPI app. Peer traffic goes through a
`RouterTransport` that forwards signed HTTP requests to the target node's ASGI app via
TestClient - so the full protocol stack (signatures, gzip, replay checks, link simulation)
is exercised, just without sockets.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relayaid.api import create_app  # noqa: E402
from relayaid.config import create_deployment, load_node_config  # noqa: E402

PASS = "test-passphrase"
NODES = [
    {"node_id": "hub", "team": "Central Server", "role": "server", "port": 9000},
    {"node_id": "alpha", "team": "Rescue Team North", "role": "node", "port": 9001},
    {"node_id": "bravo", "team": "Rescue Team South", "role": "node", "port": 9002},
    {"node_id": "charlie", "team": "Relief Coordination", "role": "node", "port": 9003},
]


class RouterTransport:
    def __init__(self):
        self.clients: dict[str, TestClient] = {}
        self.requests = 0

    def post(self, base_url, path, body, headers):
        self.requests += 1
        client = self.clients[base_url]
        r = client.post(path, content=body, headers=headers)
        return r.status_code, dict(r.headers), r.content


class Cluster:
    def __init__(self, root: Path, template: Path | None = None):
        if template is not None:  # reuse provisioned keys; every test still gets fresh databases
            shutil.copytree(template, root)
            self.configs = {n["node_id"]: load_node_config(root, n["node_id"]) for n in NODES}
        else:
            self.configs = create_deployment(root, NODES, passphrase=PASS)
        self.transport = RouterTransport()
        self.apps, self.clients, self.tokens = {}, {}, {}
        for nid, cfg in self.configs.items():
            app = create_app(cfg, transport=self.transport, background_sync=False)
            app.state.engine.backoff_base = 0.001
            client = TestClient(app)
            self.apps[nid], self.clients[nid] = app, client
            self.transport.clients[cfg.url] = client

    def node(self, nid):
        return self.apps[nid].state.node

    def engine(self, nid):
        return self.apps[nid].state.engine

    def login(self, nid, user="coordinator"):
        key = (nid, user)
        if key not in self.tokens:
            r = self.clients[nid].post("/api/auth/login", json={"username": user, "passphrase": PASS})
            assert r.status_code == 200, r.text
            self.tokens[key] = r.json()["token"]
        return {"Authorization": f"Bearer {self.tokens[key]}"}

    def api(self, nid, method, path, user="coordinator", **kw):
        return self.clients[nid].request(method, path, headers=self.login(nid, user), **kw)

    def ok(self, nid, method, path, user="coordinator", **kw):
        r = self.api(nid, method, path, user, **kw)
        assert r.status_code == 200, f"{method} {path} on {nid}: {r.status_code} {r.text}"
        return r.json()

    def sync(self, a, b):
        return self.engine(a).sync_with(b)

    def partition(self, *groups):
        """Partition the field nodes into isolated groups, e.g. partition(['alpha'], ['bravo','charlie'])."""
        allnodes = list(self.configs)
        for g in groups:
            for nid in g:
                self.node(nid).sim.update(blocked_peers=[x for x in allnodes if x not in g])

    def heal(self):
        for nid in self.configs:
            self.node(nid).sim.update(blocked_peers=[], offline=False)

    def full_mesh_sync(self, rounds=2, nodes=("alpha", "bravo", "charlie")):
        for _ in range(rounds):
            for a in nodes:
                for b in nodes:
                    if a != b:
                        self.sync(a, b)

    def digests(self, nodes=("alpha", "bravo", "charlie")):
        return {n: self.node(n).state_digest()["state"] for n in nodes}


@pytest.fixture(scope="session")
def deployment_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("template") / "deployment"
    create_deployment(root, NODES, passphrase=PASS)
    return root


@pytest.fixture
def cluster(tmp_path, deployment_template):
    c = Cluster(tmp_path / "deployment", deployment_template)
    yield c
    for nid in c.configs:
        c.node(nid).db.close()
