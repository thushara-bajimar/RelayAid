"""Convergence under partitions, reordering and duplication - compared on real state digests."""
import random

import pytest

from relayaid.node import Node
from relayaid.projection import Ev, Projection

FIELD = ("alpha", "bravo", "charlie")


def _event_ids(c, n):
    return set(c.node(n)._envelopes)


def test_scenario_1_independent_offline_updates(cluster):
    c = cluster
    c.partition(["alpha"], ["bravo"], ["charlie"])
    for n in ("alpha", "bravo", "charlie"):
        c.node(n).sim.update(blocked_peers=["alpha", "bravo", "charlie", "hub"])
    need = c.ok("alpha", "POST", "/api/needs", json={"category": "medical", "description": "First aid", "quantity": 5})
    sh = c.ok("bravo", "POST", "/api/shelters", json={"name": "Hall B", "capacity": 40})
    c.ok("bravo", "POST", f"/api/shelters/{sh['shelter_id']}/occupancy", json={"delta": 12})
    sp = c.ok("charlie", "POST", "/api/supplies", json={"name": "Food packets", "kind": "quantity", "quantity": 400})
    c.heal()
    c.full_mesh_sync()
    for n in FIELD:
        assert {x["need_id"] for x in c.ok(n, "GET", "/api/needs")} == {need["need_id"]}
        assert c.ok(n, "GET", "/api/shelters")[0]["occupancy"] == 12
        assert {x["supply_id"] for x in c.ok(n, "GET", "/api/supplies")} == {sp["supply_id"]}
    assert len(set(c.digests().values())) == 1


def test_scenario_4_repeated_partitions(cluster):
    c = cluster
    c.partition(["alpha"], ["bravo"], ["charlie"])
    c.ok("alpha", "POST", "/api/needs", json={"category": "water", "description": "A1"})
    c.ok("bravo", "POST", "/api/needs", json={"category": "food", "description": "B1"})
    c.ok("charlie", "POST", "/api/needs", json={"category": "shelter", "description": "C1"})
    c.partition(["alpha", "bravo"], ["charlie"])  # reconnect only two nodes
    c.sync("alpha", "bravo")
    c.partition(["alpha"], ["bravo"], ["charlie"])  # disconnect again
    c.ok("alpha", "POST", "/api/needs", json={"category": "water", "description": "A2"})
    c.ok("bravo", "POST", "/api/needs", json={"category": "food", "description": "B2"})
    c.partition(["bravo", "charlie"], ["alpha"])  # third node rejoins through bravo
    c.sync("charlie", "bravo")
    c.heal()
    for _ in range(3):
        c.full_mesh_sync(rounds=1)
    descriptions = {n: sorted(x["description"] for x in c.ok(n, "GET", "/api/needs")) for n in FIELD}
    assert all(d == ["A1", "A2", "B1", "B2", "C1"] for d in descriptions.values())
    assert len(set(c.digests().values())) == 1


@pytest.mark.parametrize("seed", range(6))
def test_randomized_partitions_converge_without_lost_updates(cluster, seed):
    """Property-style test: random concurrent operations on three nodes, random partitions,
    random sync orders, duplicated and shuffled delivery, then heal and compare."""
    c = cluster
    rng = random.Random(seed)
    sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "Shelter", "capacity": 200})["shelter_id"]
    kits = c.ok("alpha", "POST", "/api/supplies", json={"name": "Kits", "kind": "quantity", "quantity": 90})["supply_id"]
    c.ok("alpha", "POST", f"/api/supplies/{kits}/transfer", json={"to_node": "bravo", "amount": 30})
    c.ok("alpha", "POST", f"/api/supplies/{kits}/transfer", json={"to_node": "charlie", "amount": 30})
    boat = c.ok("alpha", "POST", "/api/supplies?supply_id=R-01",
                json={"name": "Boat", "kind": "indivisible"})["supply_id"]
    nid = c.ok("alpha", "POST", "/api/needs", json={"category": "food", "description": "base"})["need_id"]
    c.full_mesh_sync()

    for n in FIELD:
        c.node(n).sim.update(duplicate_delivery=rng.random() < 0.5, shuffle=rng.random() < 0.5,
                             batch_size=rng.choice([3, 10, 100]))
    for _round in range(5):
        groups = rng.choice([[["alpha"], ["bravo"], ["charlie"]], [["alpha", "bravo"], ["charlie"]],
                             [["alpha"], ["bravo", "charlie"]], [["alpha", "charlie"], ["bravo"]]])
        c.partition(*groups)
        for _ in range(rng.randint(3, 8)):
            n = rng.choice(FIELD)
            op = rng.randrange(6)
            if op == 0:
                c.ok(n, "POST", "/api/needs", json={"category": "water", "description": f"{n}-{rng.random():.4f}"})
            elif op == 1:
                c.ok(n, "POST", f"/api/needs/{nid}/notes", json={"text": f"{n} note {rng.random():.4f}"})
            elif op == 2:
                c.api(n, "PATCH", f"/api/needs/{nid}", json={"quantity": rng.randint(1, 500),
                                                             "priority": rng.choice(["low", "high"])})
            elif op == 3:
                c.ok(n, "POST", f"/api/shelters/{sid}/occupancy", json={"delta": rng.randint(1, 9)})
            elif op == 4:
                c.api(n, "POST", f"/api/supplies/{kits}/allocate", json={"amount": rng.randint(1, 12)})
            else:
                c.api(n, "POST", f"/api/supplies/{boat}/allocate", json={})
        pairs = [(a, b) for a in FIELD for b in FIELD if a != b]
        rng.shuffle(pairs)
        for a, b in pairs[: rng.randint(1, len(pairs))]:
            c.sync(a, b)

    c.heal()
    for n in FIELD:
        c.node(n).sim.update(window_batches=0)
    c.full_mesh_sync(rounds=2)

    ids = {n: _event_ids(c, n) for n in FIELD}
    assert ids["alpha"] == ids["bravo"] == ids["charlie"], "every update reached every node"
    assert len(set(c.digests().values())) == 1, "identical event sets produced identical state"
    occ = {c.ok(n, "GET", "/api/shelters")[0]["occupancy"] for n in FIELD}
    deltas = sum(e.payload["delta"] for e in c.node("alpha")._events if e.type == "shelter.occupancy_changed")
    assert occ == {deltas}, "every concurrent occupancy change counted exactly once"
    for n in FIELD:
        v = c.ok(n, "GET", "/api/allocations/verify")
        assert v["ok"], v["problems"]
        assert v["invalid_allocation_events_rejected"] == 0  # local checks never produced an unsafe event
        supplies = {s["supply_id"]: s for s in c.ok(n, "GET", "/api/supplies")}
        assert supplies[kits]["reserved"] <= 90
        assert len([a for a in supplies[boat]["allocations"] if a["status"] == "reserved"]) <= 1


def test_projection_is_independent_of_arrival_order(cluster):
    c = cluster
    c.partition(["alpha"], ["bravo"], ["charlie"])
    for n in FIELD:
        for i in range(5):
            c.ok(n, "POST", "/api/needs", json={"category": "food", "description": f"{n}{i}"})
    c.heal()
    c.full_mesh_sync()
    envs = list(c.node("alpha")._envelopes.values())
    reference = c.node("alpha").state_digest()["state"]
    for seed in range(3):
        random.Random(seed).shuffle(envs)
        cfg = c.configs["charlie"]
        (cfg.data_dir / f"replay{seed}").mkdir()
        fresh = Node(type(cfg)(**{**cfg.__dict__, "data_dir": cfg.data_dir / f"replay{seed}"}))
        for e in envs:  # one at a time, worst-case order
            fresh.ingest([e], received_from="replay")
        assert fresh.state_digest()["state"] == reference
        fresh.db.close()


def test_conflict_set_converges_too(cluster):
    c = cluster
    sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "S", "capacity": 10})["shelter_id"]
    c.full_mesh_sync()
    c.partition(["alpha"], ["bravo"], ["charlie"])
    for n, cap in zip(FIELD, (11, 12, 13)):
        c.ok(n, "PATCH", f"/api/shelters/{sid}", json={"capacity": cap})
    c.heal()
    c.sync("charlie", "alpha")
    c.sync("bravo", "charlie")
    c.sync("alpha", "bravo")
    conflicts = {n: sorted((x["conflict_id"], x["status"], len(x["candidates"]))
                           for x in c.ok(n, "GET", "/api/conflicts")) for n in FIELD}
    assert conflicts["alpha"] == conflicts["bravo"] == conflicts["charlie"]
    assert conflicts["alpha"][0][2] == 3  # all three competing capacities preserved


def test_happened_before_is_vector_clock_based():
    a = Ev("a", "alpha", 1, 1, {"alpha": 1}, "need", "n", "need.created", {}, "")
    b = Ev("b", "bravo", 1, 2, {"alpha": 1, "bravo": 1}, "need", "n", "need.updated", {}, "")
    c = Ev("c", "charlie", 1, 2, {"alpha": 1, "charlie": 1}, "need", "n", "need.updated", {}, "")
    from relayaid.projection import happened_before
    assert happened_before(a, b) and happened_before(a, c)
    assert not happened_before(b, c) and not happened_before(c, b)  # concurrent
    assert Projection([]).applied == 0
