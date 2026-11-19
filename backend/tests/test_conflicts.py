"""Semantic conflict resolution: nothing is silently lost, unsafe merges stay visible."""
import pytest

NEED = {"category": "food", "description": "Food packets", "quantity": 100, "unit": "packets", "priority": "medium"}


def _split(c):
    c.partition(["alpha"], ["bravo"], ["charlie"])


def _open(c, node, field=None):
    return [x for x in c.ok(node, "GET", "/api/conflicts") if x["status"] == "open"
            and (field is None or x["field"] == field)]


def test_concurrent_lww_edit_is_deterministic_and_recorded(cluster):
    c = cluster
    nid = c.ok("alpha", "POST", "/api/needs", json=NEED)["need_id"]
    c.sync("bravo", "alpha")
    _split(c)
    c.ok("alpha", "PATCH", f"/api/needs/{nid}", json={"location": "North gate"})
    c.ok("bravo", "PATCH", f"/api/needs/{nid}", json={"location": "Community hall"})
    c.heal()
    c.sync("alpha", "bravo")  # one order ...
    c.sync("charlie", "bravo")  # ... charlie learns via a different path
    loc = {n: c.ok(n, "GET", "/api/needs")[0]["location"] for n in ("alpha", "bravo", "charlie")}
    assert len(set(loc.values())) == 1
    conflicts = c.ok("alpha", "GET", "/api/conflicts")
    rec = next(x for x in conflicts if x["field"] == "location")
    assert rec["status"] == "auto_merged" and rec["resolution_kind"] == "auto_lww"
    assert {cand["value"] for cand in rec["candidates"]} == {"North gate", "Community hall"}


def test_independent_notes_are_all_preserved(cluster):
    c = cluster
    nid = c.ok("alpha", "POST", "/api/needs", json=NEED)["need_id"]
    c.full_mesh_sync()
    _split(c)
    for n in ("alpha", "bravo", "charlie"):
        c.ok(n, "POST", f"/api/needs/{nid}/notes", json={"text": f"note from {n}"})
    c.heal()
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        texts = sorted(x["text"] for x in c.ok(n, "GET", "/api/needs")[0]["notes"])
        assert texts == ["note from alpha", "note from bravo", "note from charlie"]


def test_priority_merges_to_highest(cluster):
    c = cluster
    nid = c.ok("alpha", "POST", "/api/needs", json=NEED)["need_id"]
    c.sync("bravo", "alpha")
    _split(c)
    c.ok("alpha", "PATCH", f"/api/needs/{nid}", json={"priority": "critical"})
    c.ok("bravo", "PATCH", f"/api/needs/{nid}", json={"priority": "low"})
    c.heal()
    c.full_mesh_sync()
    assert {c.ok(n, "GET", "/api/needs")[0]["priority"] for n in ("alpha", "bravo", "charlie")} == {"critical"}


def test_person_found_is_not_overwritten_by_concurrent_missing(cluster):
    c = cluster
    pid = c.ok("alpha", "POST", "/api/people", json={"name": "Ravi K", "status": "missing"})["person_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("bravo", "POST", f"/api/people/{pid}/status", json={"status": "found", "evidence": "Seen at relief camp B"})
    c.ok("alpha", "POST", f"/api/people/{pid}/status", json={"status": "missing", "evidence": "Family still searching"})
    c.heal()
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        rec = c.ok(n, "GET", "/api/people")[0]
        assert rec["verification"] == "disputed"  # never presented as confirmed truth
        assert len(rec["evidence"]) == 2  # both pieces of evidence kept
    conflict = _open(c, "charlie", "status")[0]
    assert {x["value"] for x in conflict["candidates"]} == {"found", "missing"}
    # a plain status update cannot bypass the dispute
    r = c.api("charlie", "POST", f"/api/people/{pid}/status", json={"status": "missing"})
    assert r.status_code == 409


def test_unresolved_conflict_survives_repeated_sync(cluster):
    c = cluster
    sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "Temple hall", "capacity": 80})["shelter_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("alpha", "PATCH", f"/api/shelters/{sid}", json={"capacity": 60})
    c.ok("bravo", "PATCH", f"/api/shelters/{sid}", json={"capacity": 100})
    c.heal()
    for _ in range(4):
        c.full_mesh_sync()
    ids = {n: [x["conflict_id"] for x in _open(c, n, "capacity")] for n in ("alpha", "bravo", "charlie")}
    assert all(len(v) == 1 for v in ids.values()) and len({v[0] for v in ids.values()}) == 1
    assert "capacity" in c.ok("charlie", "GET", "/api/shelters")[0]["disputed_fields"]
    # direct edits of the disputed critical field are refused
    assert c.api("charlie", "PATCH", f"/api/shelters/{sid}", json={"capacity": 70}).status_code == 409


def test_resolution_creates_event_and_converges(cluster):
    c = cluster
    sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "Temple hall", "capacity": 80})["shelter_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("alpha", "PATCH", f"/api/shelters/{sid}", json={"capacity": 60})
    c.ok("bravo", "PATCH", f"/api/shelters/{sid}", json={"capacity": 100})
    c.heal()
    c.full_mesh_sync()
    cid = _open(c, "charlie")[0]["conflict_id"]
    # responders cannot resolve; coordinators can
    assert c.api("charlie", "POST", f"/api/conflicts/{cid}/resolve", user="responder",
                 json={"value": 60, "rationale": "x"}).status_code == 403
    c.ok("charlie", "POST", f"/api/conflicts/{cid}/resolve",
         json={"value": 60, "rationale": "Engineer inspection: east wing unsafe"})
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        rec = next(x for x in c.ok(n, "GET", "/api/conflicts") if x["conflict_id"] == cid)
        assert rec["status"] == "resolved" and rec["resolution_kind"] == "explicit"
        assert rec["rationale"].startswith("Engineer inspection")
        assert len(rec["candidates"]) == 2  # history of the competing values kept
        assert c.ok(n, "GET", "/api/shelters")[0]["capacity"] == 60
    hist = c.ok("alpha", "GET", f"/api/history/shelter/{sid}")
    assert [h["type"] for h in hist].count("conflict.resolved") == 1
    assert len(set(c.digests().values())) == 1


def test_resolution_racing_a_new_concurrent_write_keeps_conflict_open(cluster):
    c = cluster
    sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "Depot", "capacity": 50})["shelter_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("alpha", "PATCH", f"/api/shelters/{sid}", json={"status": "full"})
    c.ok("bravo", "PATCH", f"/api/shelters/{sid}", json={"status": "closed"})
    c.heal()
    c.sync("alpha", "bravo")
    cid = _open(c, "alpha")[0]["conflict_id"]
    _split(c)
    c.ok("alpha", "POST", f"/api/conflicts/{cid}/resolve", json={"value": "full", "rationale": "count done"})
    c.ok("charlie", "PATCH", f"/api/shelters/{sid}", json={"status": "open"})  # charlie never saw the dispute
    c.heal()
    c.full_mesh_sync()
    still_open = _open(c, "bravo", "status")
    assert len(still_open) == 1  # the late concurrent write is not swallowed by the resolution
    assert "open" in {x["value"] for x in still_open[0]["candidates"]}


def test_concurrent_occupancy_changes_both_count_and_overflow_is_surfaced(cluster):
    c = cluster
    sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "Stadium", "capacity": 100})["shelter_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("alpha", "POST", f"/api/shelters/{sid}/occupancy", json={"delta": 60, "reason": "bus from north"})
    c.ok("bravo", "POST", f"/api/shelters/{sid}/occupancy", json={"delta": 55, "reason": "families from river"})
    c.heal()
    c.full_mesh_sync()
    for n in ("alpha", "bravo", "charlie"):
        sh = c.ok(n, "GET", "/api/shelters")[0]
        assert sh["occupancy"] == 115 and sh["available_space"] == 0
    inv = [x for x in c.ok("alpha", "GET", "/api/conflicts") if x["kind"] == "invariant"]
    assert inv and inv[0]["status"] == "open" and inv[0]["current_value"]["occupancy"] == 115
    c.ok("alpha", "POST", f"/api/conflicts/{inv[0]['conflict_id']}/acknowledge",
         json={"note": "15 moved to overflow tent"})
    c.ok("alpha", "POST", f"/api/shelters/{sid}/occupancy", json={"delta": -15, "reason": "moved to tent"})
    c.full_mesh_sync()
    rec = next(x for x in c.ok("charlie", "GET", "/api/conflicts") if x["kind"] == "invariant")
    assert rec["status"] == "resolved" and rec["resolution_kind"] == "invariant_restored"


def test_task_assignment_conflict_and_status_lattice(cluster):
    c = cluster
    t1 = c.ok("alpha", "POST", "/api/teams", json={"name": "Boat crew"})["team_id"]
    t2 = c.ok("alpha", "POST", "/api/teams", json={"name": "Medics"})["team_id"]
    task = c.ok("alpha", "POST", "/api/tasks", json={"title": "Evacuate block C"})["task_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("alpha", "POST", f"/api/tasks/{task}/assign", json={"team_id": t1})
    c.ok("bravo", "POST", f"/api/tasks/{task}/assign", json={"team_id": t2})
    c.ok("bravo", "POST", f"/api/tasks/{task}/status", json={"status": "in_progress"})
    c.heal()
    c.full_mesh_sync()
    t = c.ok("charlie", "GET", "/api/tasks")[0]
    assert t["status"] == "in_progress"  # lattice join: assigned < in_progress
    assert "assigned_team" in t["disputed_fields"]
    assert _open(c, "charlie", "assigned_team")


def test_completed_vs_cancelled_is_a_real_conflict(cluster):
    c = cluster
    task = c.ok("alpha", "POST", "/api/tasks", json={"title": "Clear road"})["task_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("alpha", "POST", f"/api/tasks/{task}/status", json={"status": "completed"})
    c.ok("bravo", "POST", f"/api/tasks/{task}/status", json={"status": "cancelled"})
    c.heal()
    c.full_mesh_sync()
    assert _open(c, "alpha", "status")


@pytest.mark.parametrize("order", [("alpha", "bravo", "charlie"), ("charlie", "bravo", "alpha"),
                                   ("bravo", "charlie", "alpha")])
def test_merge_order_does_not_change_state(cluster, order):
    c = cluster
    nid = c.ok("alpha", "POST", "/api/needs", json=NEED)["need_id"]
    c.full_mesh_sync()
    _split(c)
    c.ok("alpha", "PATCH", f"/api/needs/{nid}", json={"quantity": 120, "status": "in_progress"})
    c.ok("bravo", "PATCH", f"/api/needs/{nid}", json={"quantity": 90, "status": "fulfilled"})
    c.ok("charlie", "PATCH", f"/api/needs/{nid}", json={"description": "Food packets (veg)"})
    c.heal()
    a, b, d = order
    c.sync(a, b)
    c.sync(d, b)
    c.sync(a, d)
    c.sync(b, a)
    assert len(set(c.digests().values())) == 1
    assert _open(c, "alpha", "status")  # status is critical -> stays visible
