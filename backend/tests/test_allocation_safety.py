"""Scarce-resource safety: escrow rights and exclusive tokens under partitions."""
from relayaid import events as E

BOAT = {"name": "Rescue boat R-01", "category": "boat", "kind": "indivisible", "unit": "boat",
        "location": "Jetty 2"}
KITS = {"name": "Medical kits", "category": "medical", "kind": "quantity", "quantity": 50, "unit": "kits"}


def _register(c, node, body, sid=None):
    path = "/api/supplies" + (f"?supply_id={sid}" if sid else "")
    return c.ok(node, "POST", path, json=body)["supply_id"]


def _allocs(c, node, sid):
    s = next(x for x in c.ok(node, "GET", "/api/supplies") if x["supply_id"] == sid)
    return [a for a in s["allocations"] if a["status"] == "reserved"], s


def _forge(c, signer_node, entity_id, event_type, payload):
    """Build a validly *signed* event that bypasses the sender's local checks (a buggy or
    malicious node). The projection must still refuse it."""
    n = c.node(signer_node)
    seq = n.vv.get(signer_node, 0) + 1
    vv = {**n.vv, signer_node: seq}
    return E.build_event(origin=signer_node, seq=seq, lamport=n.lamport + 1, vv=vv, entity_type="supply",
                         entity_id=entity_id, event_type=event_type, payload=payload, signer=n.signer)


# ----------------------------------------------------------------------------- indivisible
def test_double_allocation_attempt_during_partition_is_blocked(cluster):
    """Scenario 3 from the brief."""
    c = cluster
    sid = _register(c, "alpha", BOAT, "R-01")
    c.full_mesh_sync()
    c.partition(["alpha"], ["bravo"], ["charlie"])
    ok = c.api("alpha", "POST", f"/api/supplies/{sid}/allocate", json={"note": "evacuate ward 3"})
    assert ok.status_code == 200
    blocked = c.api("bravo", "POST", f"/api/supplies/{sid}/allocate", json={"note": "evacuate school"})
    assert blocked.status_code == 409 and "does not hold the exclusive allocation authority" in blocked.json()["detail"]
    assert c.node("bravo").db.counters()["allocations_blocked"] == 1
    c.heal()
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        active, s = _allocs(c, n, sid)
        assert len(active) == 1 and active[0]["node_id"] == "alpha" and s["status"] == "allocated"
        assert c.ok(n, "GET", "/api/allocations/verify")["ok"]


def test_token_transfer_moves_authority_exactly_once(cluster):
    c = cluster
    sid = _register(c, "alpha", BOAT, "R-01")
    c.full_mesh_sync()
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo"})
    # sender lost authority immediately, even before anyone else hears about it
    assert c.api("alpha", "POST", f"/api/supplies/{sid}/allocate", json={}).status_code == 409
    assert c.api("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "charlie"}).status_code == 409
    # bravo cannot use it until the transfer event reaches it
    assert c.api("bravo", "POST", f"/api/supplies/{sid}/allocate", json={}).status_code == 409
    c.sync("bravo", "alpha")
    assert c.api("bravo", "POST", f"/api/supplies/{sid}/allocate", json={}).status_code == 200
    # charlie is stale (never saw the transfer) and still cannot claim the boat
    assert c.api("charlie", "POST", f"/api/supplies/{sid}/allocate", json={}).status_code == 409
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        active, s = _allocs(c, n, sid)
        assert s["holder"] == "bravo" and s["epoch"] == 1 and len(active) == 1
        assert len(s["transfers"]) == 1


def test_duplicate_transfer_message_does_not_duplicate_authority(cluster):
    c = cluster
    sid = _register(c, "alpha", BOAT, "R-01")
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo"})
    events = list(c.node("alpha")._envelopes.values())
    for _ in range(3):
        c.node("bravo").ingest(events, received_from="alpha")
    s = next(x for x in c.ok("bravo", "GET", "/api/supplies") if x["supply_id"] == sid)
    assert s["holder"] == "bravo" and s["epoch"] == 1 and len(s["transfers"]) == 1


def test_forged_allocation_without_token_is_rejected_everywhere(cluster):
    c = cluster
    sid = _register(c, "alpha", BOAT, "R-01")
    c.full_mesh_sync()
    c.ok("alpha", "POST", f"/api/supplies/{sid}/allocate", json={})
    forged = _forge(c, "bravo", sid, "supply.allocated",
                    {"allocation_id": "al_forged", "amount": 1, "target_type": "none", "target_id": None,
                     "epoch": 0, "note": "bypassing local checks"})
    c.node("bravo").ingest([forged], received_from="test")  # signed by bravo, so it replicates
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        active, _ = _allocs(c, n, sid)
        assert [a["allocation_id"] for a in active] != ["al_forged"] and len(active) == 1
        v = c.ok(n, "GET", "/api/allocations/verify")
        assert v["ok"] and v["invalid_allocation_events_rejected"] == 1


def test_stale_token_transfer_after_handover_is_rejected(cluster):
    c = cluster
    sid = _register(c, "alpha", BOAT, "R-01")
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo"})
    stale = _forge(c, "alpha", sid, "supply.token_transferred",
                   {"transfer_id": "tr_stale", "to_node": "charlie", "epoch": 0, "request_id": None})
    c.node("alpha").ingest([stale], received_from="test")
    c.full_mesh_sync()
    s = next(x for x in c.ok("charlie", "GET", "/api/supplies") if x["supply_id"] == sid)
    assert s["holder"] == "bravo"


def test_release_and_request_workflow(cluster):
    c = cluster
    sid = _register(c, "alpha", BOAT, "R-01")
    aid = c.ok("alpha", "POST", f"/api/supplies/{sid}/allocate", json={})["allocation_id"]
    c.sync("bravo", "alpha")
    assert c.api("bravo", "POST", f"/api/allocations/{aid}/release").status_code == 403
    rid = c.ok("bravo", "POST", f"/api/supplies/{sid}/request-rights", json={"reason": "school flooding"})["request_id"]
    c.sync("alpha", "bravo")
    assert c.api("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo"}).status_code == 409
    c.ok("alpha", "POST", f"/api/allocations/{aid}/release")
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo", "request_id": rid})
    c.sync("bravo", "alpha")
    s = next(x for x in c.ok("bravo", "GET", "/api/supplies") if x["supply_id"] == sid)
    assert s["holder"] == "bravo" and s["requests"][0]["status"] == "fulfilled"


# ----------------------------------------------------------------------------- quantity escrow
def test_cannot_allocate_more_than_local_rights(cluster):
    c = cluster
    sid = _register(c, "alpha", KITS)
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo", "amount": 20})
    c.full_mesh_sync()
    c.partition(["alpha"], ["bravo"], ["charlie"])
    assert c.api("bravo", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 21}).status_code == 409
    assert c.api("bravo", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 20}).status_code == 200
    assert c.api("alpha", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 30}).status_code == 200
    assert c.api("alpha", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 1}).status_code == 409
    # charlie never received rights: it can coordinate but not allocate
    r = c.api("charlie", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 1})
    assert r.status_code == 409 and "rights cannot be created offline" in r.json()["detail"]
    c.heal()
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        _, s = _allocs(c, n, sid)
        assert s["reserved"] == 50 and s["available"] == 0 and s["total"] == 50
        assert c.ok(n, "GET", "/api/allocations/verify")["ok"]


def test_sender_cannot_reuse_transferred_rights(cluster):
    c = cluster
    sid = _register(c, "alpha", KITS)
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo", "amount": 50})
    assert c.api("alpha", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 1}).status_code == 409
    assert c.api("alpha", "POST", f"/api/supplies/{sid}/transfer",
                 json={"to_node": "charlie", "amount": 1}).status_code == 409


def test_duplicate_rights_transfer_and_allocation_events_are_idempotent(cluster):
    c = cluster
    sid = _register(c, "alpha", KITS)
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo", "amount": 10})
    c.ok("alpha", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 5})
    evs = list(c.node("alpha")._envelopes.values())
    for _ in range(4):
        c.node("bravo").ingest(evs, received_from="alpha")
    _, s = _allocs(c, "bravo", sid)
    assert s["rights"] == {"alpha": 35, "bravo": 10} and s["reserved"] == 5 and len(s["allocations"]) == 1


def test_forged_overspend_is_rejected_by_every_replica(cluster):
    c = cluster
    sid = _register(c, "alpha", KITS)
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo", "amount": 10})
    c.full_mesh_sync()
    forged = _forge(c, "bravo", sid, "supply.allocated", {"allocation_id": "al_over", "amount": 11,
                                                          "target_type": "none", "target_id": None, "epoch": None,
                                                          "note": ""})
    c.node("bravo").ingest([forged], received_from="test")
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        _, s = _allocs(c, n, sid)
        assert s["reserved"] == 0 and s["rights"]["bravo"] == 10


def test_rights_not_yet_received_cannot_be_spent(cluster):
    """Causal check: a node may only spend credits in its causal past."""
    c = cluster
    sid = _register(c, "alpha", KITS)
    c.sync("bravo", "alpha")
    c.ok("alpha", "POST", f"/api/supplies/{sid}/transfer", json={"to_node": "bravo", "amount": 10})
    # bravo has not received the transfer; a forged spend referencing it must fail
    forged = _forge(c, "bravo", sid, "supply.allocated", {"allocation_id": "al_early", "amount": 5,
                                                          "target_type": "none", "target_id": None, "epoch": None,
                                                          "note": ""})
    c.node("bravo").ingest([forged], received_from="test")
    c.full_mesh_sync()
    _, s = _allocs(c, "charlie", sid)
    assert s["reserved"] == 0
    assert c.ok("charlie", "GET", "/api/allocations/verify")["invalid_allocation_events_rejected"] == 1


def test_distribution_and_accounting_balance(cluster):
    c = cluster
    sid = _register(c, "alpha", {**KITS, "quantity": 100})
    a1 = c.ok("alpha", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 30})["allocation_id"]
    a2 = c.ok("alpha", "POST", f"/api/supplies/{sid}/allocate", json={"amount": 20})["allocation_id"]
    c.ok("alpha", "POST", f"/api/allocations/{a1}/distribute")
    c.ok("alpha", "POST", f"/api/allocations/{a2}/release")
    c.ok("alpha", "POST", f"/api/supplies/{sid}/write-off", json={"amount": 5, "note": "water damage"})
    c.ok("alpha", "POST", f"/api/supplies/{sid}/restock", json={"amount": 10})
    _, s = _allocs(c, "alpha", sid)
    assert (s["total"], s["available"], s["reserved"], s["distributed"], s["unavailable"]) == (110, 75, 0, 30, 5)
    assert c.ok("alpha", "GET", "/api/allocations/verify")["ok"]
