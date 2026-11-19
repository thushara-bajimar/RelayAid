"""Offline operation and synchronization protocol tests."""
from relayaid.node import Node

NEED = {"category": "water", "description": "Drinking water for 40 people", "quantity": 40, "unit": "litres",
        "priority": "high", "location": "Ward 7"}


# ----------------------------------------------------------------------------- offline operation
def test_node_operates_fully_offline_without_server(cluster):
    c = cluster
    c.node("alpha").sim.update(offline=True)  # no peers, no server
    need = c.ok("alpha", "POST", "/api/needs", json=NEED)
    c.ok("alpha", "PATCH", f"/api/needs/{need['need_id']}", json={"priority": "critical"})
    c.ok("alpha", "POST", f"/api/needs/{need['need_id']}/notes", json={"text": "Tanker blocked by flood"})
    team = c.ok("alpha", "POST", "/api/teams", json={"name": "Boat crew 1", "specialty": "water rescue", "members": 4})
    task = c.ok("alpha", "POST", "/api/tasks", json={"title": "Deliver water", "need_id": need["need_id"]})
    c.ok("alpha", "POST", f"/api/tasks/{task['task_id']}/assign", json={"team_id": team["team_id"]})
    c.ok("alpha", "POST", "/api/shelters", json={"name": "School", "capacity": 120})
    c.ok("alpha", "POST", "/api/supplies", json={"name": "Water packets", "kind": "quantity", "quantity": 300})
    c.ok("alpha", "POST", "/api/people", json={"name": "Asha R", "status": "missing"})

    needs = c.ok("alpha", "GET", "/api/needs")
    assert needs[0]["priority"] == "critical" and needs[0]["notes"][0]["text"] == "Tanker blocked by flood"
    assert c.ok("alpha", "GET", "/api/tasks")[0]["status"] == "assigned"
    # every attempt to reach anyone fails locally, without sending a packet
    before = c.transport.requests
    for peer in ("hub", "bravo", "charlie"):
        assert c.sync("alpha", peer)["status"] == "link_down"
    assert c.transport.requests == before
    assert c.node("hub").event_count() == 0


def test_local_data_survives_restart(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/needs", json=NEED)
    digest = c.node("alpha").state_digest()
    reopened = Node(c.configs["alpha"])  # same database file, new process-equivalent
    assert reopened.state_digest() == digest
    assert reopened.list_needs()[0]["description"] == NEED["description"]


# ----------------------------------------------------------------------------- synchronization
def test_peer_to_peer_sync_without_server(cluster):
    c = cluster
    for n in ("alpha", "bravo"):
        c.node(n).sim.update(blocked_peers=["hub", "charlie"])
    c.ok("alpha", "POST", "/api/needs", json=NEED)
    r = c.sync("bravo", "alpha")
    assert r["status"] == "success" and r["events_pulled"] == 1
    assert c.node("hub").event_count() == 0  # nothing went through the server
    assert c.ok("bravo", "GET", "/api/needs")[0]["description"] == NEED["description"]


def test_sync_through_central_server(cluster):
    c = cluster
    c.partition(["alpha", "hub"], ["charlie", "hub"])  # alpha and charlie never meet directly
    c.node("hub").sim.update(blocked_peers=[])
    c.ok("alpha", "POST", "/api/needs", json=NEED)
    assert c.sync("alpha", "hub")["events_pushed"] == 1
    assert c.sync("charlie", "hub")["events_pulled"] == 1
    assert c.sync("alpha", "charlie")["status"] == "link_down"
    assert c.ok("charlie", "GET", "/api/needs")[0]["need_id"] == c.ok("alpha", "GET", "/api/needs")[0]["need_id"]


def test_incremental_sync_only_sends_missing_events(cluster):
    c = cluster
    for i in range(30):
        c.ok("alpha", "POST", "/api/needs", json={**NEED, "description": f"need {i}"})
    first = c.sync("bravo", "alpha")
    assert first["events_pulled"] == 30
    again = c.sync("bravo", "alpha")
    assert again["events_pulled"] == 0 and again["events_pushed"] == 0
    c.ok("alpha", "POST", "/api/needs", json={**NEED, "description": "one more"})
    third = c.sync("bravo", "alpha")
    assert third["events_pulled"] == 1
    assert third["bytes_received"] < first["bytes_received"] / 5


def test_bounded_batches_and_pagination(cluster):
    c = cluster
    for i in range(25):
        c.ok("alpha", "POST", "/api/needs", json={**NEED, "description": f"need {i}"})
    c.node("bravo").sim.update(batch_size=10)
    r = c.sync("bravo", "alpha")
    assert r["status"] == "success" and r["events_pulled"] == 25 and r["batches"] >= 3


def test_duplicate_delivery_is_idempotent(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/needs", json=NEED)
    c.ok("alpha", "POST", "/api/supplies", json={"name": "Medical kits", "kind": "quantity", "quantity": 20})
    c.node("bravo").sim.update(duplicate_delivery=True)
    r = c.sync("bravo", "alpha")
    assert r["events_pulled"] == 2 and r["duplicates"] == 2
    # replaying the exact same events again changes nothing
    raw = list(c.node("alpha")._envelopes.values())
    ack = c.node("bravo").ingest(raw + raw, received_from="test")
    assert ack["accepted"] == [] and ack["duplicates"] == 4
    assert len(c.ok("bravo", "GET", "/api/needs")) == 1
    assert c.node("alpha").state_digest()["state"] == c.node("bravo").state_digest()["state"]


def test_failed_sync_recovers_with_retry_and_backoff(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/needs", json=NEED)
    c.node("bravo").sim.update(drop_rate=0.95)
    c.node("bravo").sim.rng.seed(7)
    failed = c.sync("bravo", "alpha")
    assert failed["status"] in ("failed", "partial") and failed["retries"] >= 1
    state = c.node("bravo").db.one("SELECT consecutive_failures, next_attempt_at FROM peer_state WHERE peer_id='alpha'")
    if failed["status"] == "failed":
        assert state["consecutive_failures"] >= 1 and state["next_attempt_at"] > 0
    c.node("bravo").sim.update(drop_rate=0.0)
    ok = c.sync("bravo", "alpha")
    assert ok["status"] == "success"
    assert len(c.ok("bravo", "GET", "/api/needs")) == 1


def test_partial_sync_in_short_window_resumes_later(cluster):
    c = cluster
    for i in range(12):
        c.ok("alpha", "POST", "/api/needs", json={**NEED, "description": f"need {i}"})
    c.node("bravo").sim.update(batch_size=5, window_batches=1)
    r1 = c.sync("bravo", "alpha")
    assert r1["status"] == "partial" and r1["events_pulled"] == 5
    assert c.node("bravo").version_vector() == {"alpha": 5}  # a consistent, contiguous prefix
    r2 = c.sync("bravo", "alpha")
    assert r2["events_pulled"] == 5
    c.node("bravo").sim.update(window_batches=0)
    r3 = c.sync("bravo", "alpha")
    assert r3["events_pulled"] == 2 and r3["status"] == "success"
    assert c.node("bravo").state_digest()["state"] == c.node("alpha").state_digest()["state"]


def test_receiver_side_partition_is_enforced(cluster):
    c = cluster
    c.node("bravo").sim.update(blocked_peers=["alpha"])  # only bravo cut the link
    r = c.sync("alpha", "bravo")
    assert r["status"] == "link_down"


def test_shuffled_delivery_order_gives_same_state(cluster):
    c = cluster
    need = c.ok("alpha", "POST", "/api/needs", json=NEED)
    for p in ("low", "high", "medium"):
        c.ok("alpha", "PATCH", f"/api/needs/{need['need_id']}", json={"priority": p, "quantity": 10})
    c.node("bravo").sim.update(shuffle=True, batch_size=2)
    c.sync("bravo", "alpha")
    assert c.node("bravo").state_digest()["state"] == c.node("alpha").state_digest()["state"]


def test_sync_status_and_metrics_are_measured(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/needs", json=NEED)
    c.sync("bravo", "alpha")
    m = c.ok("bravo", "GET", "/api/metrics")
    assert m["sessions"] == 1 and m["events_transferred"] == 1 and m["bytes_received"] > 0
    st = c.ok("bravo", "GET", "/api/node")
    peer = next(p for p in st["peers"] if p["node_id"] == "alpha")
    assert peer["last_success_at"] and peer["pending_events"] == 0
