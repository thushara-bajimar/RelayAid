"""Load a small, realistic demo dataset into the running nodes through their public APIs.

Every record is created on a specific node (as a real team would), then the nodes are
synchronized so the demo starts from a converged state.
"""
from __future__ import annotations

import os
import sys

import httpx

PASS = os.environ.get("RELAYAID_OPERATOR_PASSPHRASE", "relayaid-demo")
PORTS = {"alpha": 7401, "bravo": 7402, "charlie": 7403}


class Client:
    def __init__(self, node: str):
        self.node, self.base = node, f"http://127.0.0.1:{PORTS[node]}"
        r = httpx.post(self.base + "/api/auth/login", json={"username": "coordinator", "passphrase": PASS})
        r.raise_for_status()
        self.h = {"Authorization": f"Bearer {r.json()['token']}"}

    def post(self, path, body=None):
        r = httpx.post(self.base + path, json=body or {}, headers=self.h, timeout=30)
        if r.status_code != 200:
            print(f"  ! {self.node} {path}: {r.status_code} {r.text[:120]}")
            return {}
        return r.json()

    def get(self, path):
        return httpx.get(self.base + path, headers=self.h, timeout=30).json()


def sync_all(clients):
    for a in clients.values():
        for peer in ("hub", *PORTS):
            if peer != a.node:
                a.post("/api/sync/run", {"peer_id": peer})


def main() -> None:
    try:
        c = {n: Client(n) for n in PORTS}
    except httpx.HTTPError as exc:
        sys.exit(f"nodes not reachable ({exc}); start them with scripts/run_cluster.py")
    if c["alpha"].get("/api/needs"):
        print("demo data already present; skipping seed")
        return
    A, B, C = c["alpha"], c["bravo"], c["charlie"]

    north = A.post("/api/teams", {"name": "North Boat Crew", "specialty": "water rescue", "members": 5})
    medics = A.post("/api/teams", {"name": "North Medics", "specialty": "first aid", "members": 3})
    south = B.post("/api/teams", {"name": "South Search Team", "specialty": "search & rescue", "members": 6})
    logistics = C.post("/api/teams", {"name": "Relief Logistics", "specialty": "distribution", "members": 8})

    n1 = A.post("/api/needs", {"category": "water", "description": "Drinking water for 40 people at Ward 7 rooftop",
                               "quantity": 160, "unit": "litres", "priority": "critical", "location": "Ward 7",
                               "responsible_team": north.get("team_id")})
    A.post("/api/needs", {"category": "medical", "description": "First aid supplies for injured residents",
                          "quantity": 12, "unit": "kits", "priority": "high", "location": "Riverside clinic"})
    B.post("/api/needs", {"category": "evacuation", "description": "Evacuation transport for elderly residents",
                          "quantity": 18, "unit": "people", "priority": "high", "location": "South colony lane 4"})
    C.post("/api/needs", {"category": "food", "description": "Food packets for relief camp B",
                          "quantity": 300, "unit": "packets", "priority": "medium", "location": "Camp B"})

    A.post("/api/supplies?supply_id=R-01", {"name": "Rescue boat R-01", "category": "boat", "kind": "indivisible",
                                             "unit": "boat", "location": "Jetty 2"})
    B.post("/api/supplies?supply_id=A-03", {"name": "Ambulance A-03", "category": "vehicle", "kind": "indivisible",
                                             "unit": "vehicle", "location": "South depot"})
    C.post("/api/supplies?supply_id=G-02", {"name": "Generator G-02", "category": "power", "kind": "indivisible",
                                             "unit": "generator", "location": "Camp B"})
    water = C.post("/api/supplies", {"name": "Water packets (1 L)", "category": "water", "kind": "quantity",
                                     "quantity": 600, "unit": "packets", "location": "Central warehouse"})
    kits = A.post("/api/supplies", {"name": "Medical kits", "category": "medical", "kind": "quantity",
                                    "quantity": 60, "unit": "kits", "location": "North field hospital"})
    C.post("/api/supplies", {"name": "Food packets", "category": "food", "kind": "quantity", "quantity": 900,
                             "unit": "packets", "location": "Central warehouse"})
    # pre-distribute escrow rights so every team can act while disconnected
    if water:
        C.post(f"/api/supplies/{water['supply_id']}/transfer", {"to_node": "alpha", "amount": 200})
        C.post(f"/api/supplies/{water['supply_id']}/transfer", {"to_node": "bravo", "amount": 200})
    if kits:
        A.post(f"/api/supplies/{kits['supply_id']}/transfer", {"to_node": "bravo", "amount": 20})

    s1 = B.post("/api/shelters", {"name": "Govt. Higher Secondary School", "location": "South colony", "capacity": 250,
                                  "accessibility": "ramp at gate 2; ground-floor toilets",
                                  "conditions": "dry, power intermittent"})
    C.post("/api/shelters", {"name": "Relief Camp B", "location": "Hill road", "capacity": 400,
                             "accessibility": "level ground", "conditions": "tents, drinking water available"})
    if s1:
        B.post(f"/api/shelters/{s1['shelter_id']}/occupancy", {"delta": 142, "reason": "initial headcount"})

    t1 = A.post("/api/tasks", {"title": "Deliver water to Ward 7 rooftop", "priority": "critical",
                               "location": "Ward 7", "need_id": n1.get("need_id")})
    if t1:
        A.post(f"/api/tasks/{t1['task_id']}/assign", {"team_id": north.get("team_id")})
    t2 = B.post("/api/tasks", {"title": "Search lane 4 for stranded residents", "priority": "high",
                               "location": "South colony lane 4"})
    if t2:
        B.post(f"/api/tasks/{t2['task_id']}/assign", {"team_id": south.get("team_id")})
    C.post("/api/tasks", {"title": "Set up distribution counter at Camp B", "priority": "medium",
                          "location": "Camp B"})
    _ = medics, logistics

    B.post("/api/people", {"name": "Demo Person One", "age": "67", "status": "missing",
                           "description": "Fictional demo record", "last_known_location": "Lane 4, house 12",
                           "reporting_team": "South Search Team"})
    sync_all(c)
    sync_all(c)
    print("demo data loaded and synchronized")


if __name__ == "__main__":
    main()
