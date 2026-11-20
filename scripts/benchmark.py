"""RelayAid evaluation harness. Every number it prints is measured on this machine.

Experiments E1-E8 run complete four-node deployments in-process: each node has its own
SQLite file and FastAPI app, and peer traffic goes through the full signed/gzip protocol via
an ASGI transport (no sockets). E9 starts four separate OS processes and synchronizes them
over real loopback HTTP.

    python scripts/benchmark.py            # writes docs/results/benchmark_results.{json,md}
    python scripts/benchmark.py --quick    # fewer trials
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

import logging  # noqa: E402

logging.disable(logging.CRITICAL)

from conftest import NODES, PASS, Cluster  # noqa: E402
from relayaid import events as E  # noqa: E402
from relayaid.config import create_deployment  # noqa: E402

FIELD = ("alpha", "bravo", "charlie")
ORDERS = {
    "ring": [("alpha", "bravo"), ("bravo", "charlie"), ("charlie", "alpha")],
    "reverse_ring": [("charlie", "bravo"), ("bravo", "alpha"), ("alpha", "charlie")],
    "star_via_server": [("alpha", "hub"), ("bravo", "hub"), ("charlie", "hub")],
    "full_mesh": [(a, b) for a in FIELD for b in FIELD if a != b],
}

TEMPLATE: Path | None = None


def new_cluster() -> tuple[Cluster, Path]:
    global TEMPLATE
    base = Path(tempfile.mkdtemp(prefix="relayaid-bench-"))
    if TEMPLATE is None:
        TEMPLATE = Path(tempfile.mkdtemp(prefix="relayaid-tpl-")) / "deployment"
        create_deployment(TEMPLATE, NODES, passphrase=PASS)
    return Cluster(base / "d", TEMPLATE), base


def close(c: Cluster, base: Path) -> None:
    for n in c.configs:
        c.node(n).db.close()
    shutil.rmtree(base, ignore_errors=True)


def converge(c: Cluster, pairs, max_rounds=8):
    """Run sync rounds in the given order until digests match and nothing moves."""
    t0, moved, wire, raw = time.perf_counter(), 0, 0, 0
    for r in range(1, max_rounds + 1):
        round_moved = 0
        for a, b in pairs:
            s = c.sync(a, b)
            round_moved += s["events_pulled"] + s["events_pushed"]
            wire += s["bytes_sent"] + s["bytes_received"]
            raw += s["raw_bytes_sent"] + s["raw_bytes_received"]
        moved += round_moved
        if len(set(c.digests().values())) == 1 and round_moved == 0:
            return {"converged": True, "rounds": r, "ms": (time.perf_counter() - t0) * 1000,
                    "events_moved": moved, "wire_bytes": wire, "raw_bytes": raw}
    return {"converged": False, "rounds": max_rounds, "ms": (time.perf_counter() - t0) * 1000,
            "events_moved": moved, "wire_bytes": wire, "raw_bytes": raw}


def mk_need(c, n, i):
    c.ok(n, "POST", "/api/needs", json={"category": "food", "description": f"{n} need {i}", "quantity": i % 50 + 1,
                                        "unit": "packets", "location": f"Sector {i % 9}", "priority": "medium"})


# ----------------------------------------------------------------------------- E1
def e1_payload():
    rows = []
    for n_events in (10, 100, 1000):
        c, base = new_cluster()
        for i in range(n_events):
            mk_need(c, "alpha", i)
        first = c.sync("bravo", "alpha")
        mk_need(c, "alpha", 99999)
        inc = c.sync("bravo", "alpha")
        noop = c.sync("bravo", "alpha")
        rows.append({
            "events": n_events,
            "initial_sync_ms": round(first["duration_ms"], 1),
            "initial_wire_bytes": first["bytes_received"] + first["bytes_sent"],
            "initial_raw_bytes": first["raw_bytes_received"] + first["raw_bytes_sent"],
            "wire_bytes_per_event": round((first["bytes_received"] + first["bytes_sent"]) / n_events, 1),
            "compression_ratio": round((first["raw_bytes_received"] + first["raw_bytes_sent"]) /
                                       (first["bytes_received"] + first["bytes_sent"]), 2),
            "batches": first["batches"],
            "incremental_1_event_wire_bytes": inc["bytes_received"] + inc["bytes_sent"],
            "incremental_events_transferred": inc["events_pulled"],
            "noop_sync_wire_bytes": noop["bytes_received"] + noop["bytes_sent"],
            "noop_sync_ms": round(noop["duration_ms"], 1),
        })
        close(c, base)
    return rows


# ----------------------------------------------------------------------------- E2
def partitioned_workload(c, k, rng):
    sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "Shared shelter", "capacity": 10_000})["shelter_id"]
    nid = c.ok("alpha", "POST", "/api/needs", json={"category": "water", "description": "shared"})["need_id"]
    c.full_mesh_sync(rounds=1)
    c.partition(["alpha"], ["bravo"], ["charlie"])
    t_part = time.perf_counter()
    for n in FIELD:
        for i in range(k):
            r = rng.random()
            if r < 0.5:
                mk_need(c, n, i)
            elif r < 0.7:
                c.ok(n, "POST", f"/api/shelters/{sid}/occupancy", json={"delta": rng.randint(1, 5)})
            elif r < 0.85:
                c.ok(n, "POST", f"/api/needs/{nid}/notes", json={"text": f"{n} {i}"})
            else:
                c.api(n, "PATCH", f"/api/needs/{nid}", json={"location": f"{n}-{i}", "status": rng.choice(["open", "in_progress"])})
    c.heal()
    return (time.perf_counter() - t_part) * 1000


def e2_convergence(trials):
    out = []
    for order, pairs in ORDERS.items():
        for k in (20, 100):
            runs = []
            for t in range(trials):
                c, base = new_cluster()
                rng = random.Random(t)
                partition_ms = partitioned_workload(c, k, rng)
                r = converge(c, pairs)
                ids = [set(c.node(n)._envelopes) for n in FIELD]
                r["no_lost_updates"] = ids[0] == ids[1] == ids[2]
                r["open_conflicts"] = len([x for x in c.node("alpha").list_conflicts("open")])
                r["partition_ms"] = partition_ms
                runs.append(r)
                close(c, base)
            out.append({
                "order": order, "updates_per_node": k, "nodes": 3, "trials": trials,
                "converged": sum(r["converged"] for r in runs),
                "no_lost_updates": sum(r["no_lost_updates"] for r in runs),
                "mean_ms": round(statistics.mean(r["ms"] for r in runs), 1),
                "max_ms": round(max(r["ms"] for r in runs), 1),
                "mean_rounds": round(statistics.mean(r["rounds"] for r in runs), 2),
                "mean_events_moved": round(statistics.mean(r["events_moved"] for r in runs), 1),
                "mean_wire_kb": round(statistics.mean(r["wire_bytes"] for r in runs) / 1024, 1),
                "mean_open_conflicts": round(statistics.mean(r["open_conflicts"] for r in runs), 2),
            })
    return out


# ----------------------------------------------------------------------------- E3
def e3_chaos(seeds):
    results = []
    for seed in range(seeds):
        rng = random.Random(1000 + seed)
        c, base = new_cluster()
        sid = c.ok("alpha", "POST", "/api/shelters", json={"name": "S", "capacity": 500})["shelter_id"]
        nid = c.ok("alpha", "POST", "/api/needs", json={"category": "food", "description": "base"})["need_id"]
        c.full_mesh_sync(rounds=1)
        for n in FIELD:
            c.node(n).sim.update(duplicate_delivery=rng.random() < 0.5, shuffle=rng.random() < 0.5,
                                 batch_size=rng.choice([2, 5, 50]), window_batches=rng.choice([0, 1, 3]))
        writes = 0
        for _ in range(6):
            groups = rng.choice([[["alpha"], ["bravo"], ["charlie"]], [["alpha", "bravo"], ["charlie"]],
                                 [["alpha"], ["bravo", "charlie"]], [["alpha", "charlie"], ["bravo"]]])
            c.partition(*groups)
            for _ in range(rng.randint(4, 10)):
                n = rng.choice(FIELD)
                op = rng.randrange(4)
                if op == 0:
                    mk_need(c, n, writes)
                elif op == 1:
                    c.ok(n, "POST", f"/api/shelters/{sid}/occupancy", json={"delta": rng.randint(1, 4)})
                elif op == 2:
                    c.ok(n, "POST", f"/api/needs/{nid}/notes", json={"text": f"n{writes}"})
                else:
                    c.api(n, "PATCH", f"/api/shelters/{sid}", json={"capacity": rng.randint(400, 600)})
                writes += 1
            pairs = [(a, b) for a in FIELD for b in FIELD if a != b]
            rng.shuffle(pairs)
            for a, b in pairs[: rng.randint(1, 4)]:
                c.sync(a, b)
        c.heal()
        for n in FIELD:
            c.node(n).sim.update(window_batches=0)
        r = converge(c, ORDERS["full_mesh"])
        ids = [set(c.node(n)._envelopes) for n in FIELD]
        dups = sum(c.node(n).db.counters().get("duplicate_events_ignored", 0) for n in FIELD)
        conflicts = [sorted(x["conflict_id"] for x in c.node(n).list_conflicts()) for n in FIELD]
        results.append({"seed": seed, "writes": writes, "converged": r["converged"],
                        "no_lost_updates": ids[0] == ids[1] == ids[2], "events": len(ids[0]),
                        "duplicates_ignored": dups, "conflicts_identical": conflicts[0] == conflicts[1] == conflicts[2],
                        "open_conflicts": len(c.node("alpha").list_conflicts("open")), "final_ms": round(r["ms"], 1)})
        close(c, base)
    return {
        "runs": len(results),
        "converged": sum(r["converged"] for r in results),
        "no_lost_updates": sum(r["no_lost_updates"] for r in results),
        "conflict_sets_identical": sum(r["conflicts_identical"] for r in results),
        "total_writes": sum(r["writes"] for r in results),
        "total_duplicates_ignored": sum(r["duplicates_ignored"] for r in results),
        "runs_with_open_conflicts": sum(1 for r in results if r["open_conflicts"]),
        "details": results,
    }


# ----------------------------------------------------------------------------- E4
def e4_allocation(attempts):
    c, base = new_cluster()
    rng = random.Random(42)
    boat = c.ok("alpha", "POST", "/api/supplies?supply_id=R-01", json={"name": "Boat", "kind": "indivisible"})["supply_id"]
    kits = c.ok("alpha", "POST", "/api/supplies", json={"name": "Kits", "kind": "quantity", "quantity": 300})["supply_id"]
    c.ok("alpha", "POST", f"/api/supplies/{kits}/transfer", json={"to_node": "bravo", "amount": 100})
    c.ok("alpha", "POST", f"/api/supplies/{kits}/transfer", json={"to_node": "charlie", "amount": 100})
    c.full_mesh_sync(rounds=1)
    stats = {"attempts": 0, "accepted": 0, "blocked": 0, "token_transfers": 0, "releases": 0}
    for step in range(attempts):
        if step % 15 == 0:
            groups = rng.choice([[["alpha"], ["bravo"], ["charlie"]], [["alpha", "bravo"], ["charlie"]], [FIELD]])
            c.partition(*[list(g) for g in groups])
            pairs = [(a, b) for a in FIELD for b in FIELD if a != b]
            rng.shuffle(pairs)
            for a, b in pairs[:3]:
                c.sync(a, b)
        n = rng.choice(FIELD)
        kind = rng.random()
        if kind < 0.45:
            r = c.api(n, "POST", f"/api/supplies/{boat}/allocate", json={})
        elif kind < 0.9:
            r = c.api(n, "POST", f"/api/supplies/{kits}/allocate", json={"amount": rng.randint(1, 15)})
        elif kind < 0.95:
            r = c.api(n, "POST", f"/api/supplies/{boat}/transfer", json={"to_node": rng.choice([x for x in FIELD if x != n])})
            stats["token_transfers"] += r.status_code == 200
            continue
        else:
            sup = next(s for s in c.ok(n, "GET", "/api/supplies") if s["supply_id"] == boat)
            act = [a for a in sup["allocations"] if a["status"] == "reserved" and a["node_id"] == n]
            if act:
                c.api(n, "POST", f"/api/allocations/{act[0]['allocation_id']}/release")
                stats["releases"] += 1
            continue
        stats["attempts"] += 1
        stats["accepted" if r.status_code == 200 else "blocked"] += 1
    # forged events from a misbehaving node that skips local checks
    forged = 0
    for i in range(10):
        n = c.node("charlie")
        seq = n.vv.get("charlie", 0) + 1
        ev = E.build_event(origin="charlie", seq=seq, lamport=n.lamport + 1, vv={**n.vv, "charlie": seq},
                           entity_type="supply", entity_id=kits if i % 2 else boat, event_type="supply.allocated",
                           payload={"allocation_id": f"al_forged{i}", "amount": 500 if i % 2 else 1, "target_type": "none",
                                    "target_id": None, "epoch": 99, "note": ""}, signer=n.signer)
        n.ingest([ev], received_from="forger")
        forged += 1
    c.heal()
    converge(c, ORDERS["full_mesh"])
    verify = {n: c.node(n).verify_allocations() for n in FIELD}
    sup = {s["supply_id"]: s for s in c.node("alpha").list_supplies()}
    stats.update({
        "forged_events_injected": forged,
        "forged_events_rejected_by_every_replica": min(v["invalid_allocation_events_rejected"] for v in verify.values()),
        "audit_ok_on_all_nodes": all(v["ok"] for v in verify.values()),
        "boat_active_allocations": len([a for a in sup[boat]["allocations"] if a["status"] == "reserved"]),
        "kits_reserved": sup[kits]["reserved"], "kits_total": sup[kits]["total"],
        "kits_never_overallocated": sup[kits]["reserved"] <= sup[kits]["total"],
        "allocations_blocked_counter": sum(c.node(n).db.counters().get("allocations_blocked", 0) for n in FIELD),
    })
    close(c, base)
    return stats


# ----------------------------------------------------------------------------- E5/E6
def e5_duplicates():
    c, base = new_cluster()
    for i in range(200):
        mk_need(c, "alpha", i)
    for n in FIELD:
        c.node(n).sim.update(duplicate_delivery=True, batch_size=25)
    r = converge(c, ORDERS["ring"])
    out = {"events": 200, "converged": r["converged"],
           "duplicates_ignored": sum(c.node(n).db.counters().get("duplicate_events_ignored", 0) for n in FIELD),
           "needs_per_node": [len(c.node(n).list_needs()) for n in FIELD]}
    close(c, base)
    return out


def e6_lossy(trials):
    rows = []
    for drop in (0.1, 0.3, 0.5):
        conv, sessions, ok, retries = 0, 0, 0, 0
        for t in range(trials):
            c, base = new_cluster()
            for n in FIELD:
                for i in range(30):
                    mk_need(c, n, i)
                c.node(n).sim.update(drop_rate=drop, batch_size=10)
                c.node(n).sim.rng.seed(t * 10 + hash(n) % 7)
            r = converge(c, ORDERS["full_mesh"], max_rounds=12)
            conv += r["converged"]
            for n in FIELD:
                m = c.node(n).db.one("SELECT COUNT(*) s, SUM(status='success') ok, SUM(retries) rt FROM sync_sessions")
                sessions += m["s"]
                ok += m["ok"] or 0
                retries += m["rt"] or 0
            close(c, base)
        rows.append({"drop_rate": drop, "trials": trials, "converged": conv, "sessions": sessions,
                     "session_success_rate": round(ok / sessions, 3), "retries": retries})
    return rows


# ----------------------------------------------------------------------------- E7
def e7_rebuild():
    rows = []
    c, base = new_cluster()
    count = 0
    for target in (100, 1000, 3000):
        while count < target:
            mk_need(c, "alpha", count)
            count += 1
        node = c.node("alpha")
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            node.rebuild()
            times.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        mk_need(c, "alpha", count)
        count += 1
        write_ms = (time.perf_counter() - t0) * 1000
        rows.append({"events": target, "rebuild_ms_median": round(statistics.median(times), 1),
                     "local_write_ms_incl_rebuild": round(write_ms, 1)})
    close(c, base)
    return rows


# ----------------------------------------------------------------------------- E8 (real processes)
def e8_processes(k=50):
    import httpx
    base = Path(tempfile.mkdtemp(prefix="relayaid-proc-"))
    nodes = [{**n, "port": 7510 + i} for i, n in enumerate(
        [{"node_id": "hub", "team": "Server", "role": "server"}, {"node_id": "alpha", "team": "A", "role": "node"},
         {"node_id": "bravo", "team": "B", "role": "node"}, {"node_id": "charlie", "team": "C", "role": "node"}])]
    create_deployment(base / "d", nodes, passphrase=PASS)
    procs = [subprocess.Popen([sys.executable, "-m", "relayaid.serve", "--node", n["node_id"], "--runtime",
                               str(base / "d"), "--no-auto-sync"], cwd=str(ROOT / "backend"),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for n in nodes]
    port = {n["node_id"]: n["port"] for n in nodes}
    try:
        for n in nodes:
            for _ in range(100):
                try:
                    httpx.get(f"http://127.0.0.1:{n['port']}/api/health", timeout=0.5)
                    break
                except httpx.HTTPError:
                    time.sleep(0.2)
        cl = httpx.Client(timeout=30)
        tok = {n: cl.post(f"http://127.0.0.1:{port[n]}/api/auth/login",
                          json={"username": "coordinator", "passphrase": PASS}).json()["token"] for n in FIELD}
        call = lambda n, m, p, j=None: cl.request(m, f"http://127.0.0.1:{port[n]}{p}", json=j,  # noqa: E731
                                                  headers={"Authorization": f"Bearer {tok[n]}"})
        for n in FIELD:
            call(n, "PUT", "/api/sim", {"blocked_peers": [x for x in port if x != n]})
        t0 = time.perf_counter()
        for n in FIELD:
            for i in range(k):
                call(n, "POST", "/api/needs", {"category": "food", "description": f"{n} {i}"})
        write_ms = (time.perf_counter() - t0) * 1000
        for n in FIELD:
            call(n, "PUT", "/api/sim", {"blocked_peers": []})
        t0 = time.perf_counter()
        rounds, wire = 0, 0
        for rounds in range(1, 6):
            moved = 0
            for a, b in ORDERS["ring"]:
                s = call(a, "POST", "/api/sync/run", {"peer_id": b}).json()
                moved += s["events_pulled"] + s["events_pushed"]
                wire += s["bytes_sent"] + s["bytes_received"]
            digests = {n: call(n, "GET", "/api/state/digest").json()["state"] for n in FIELD}
            if len(set(digests.values())) == 1 and moved == 0:
                break
        conv_ms = (time.perf_counter() - t0) * 1000
        return {"transport": "real HTTP over loopback, 4 OS processes", "updates_per_node": k,
                "local_writes_total": 3 * k, "mean_local_write_ms": round(write_ms / (3 * k), 2),
                "converged": len(set(digests.values())) == 1, "rounds": rounds,
                "convergence_ms": round(conv_ms, 1), "wire_kb": round(wire / 1024, 1)}
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(base, ignore_errors=True)


def to_md(res):
    L = [f"# RelayAid measured results", "", f"Generated {res['generated_at']} on {res['machine']}.", "",
         "All values are measured by `scripts/benchmark.py`; E1-E7 use in-process nodes (separate SQLite files, full "
         "signed/gzip protocol over an ASGI transport), E8 uses four OS processes over loopback HTTP.", ""]

    def table(rows, cols):
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "---|" * len(cols))
        for r in rows:
            L.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
        L.append("")
    L.append("## E1 - Sync payload and incrementality")
    table(res["E1_payload"], list(res["E1_payload"][0].keys()))
    L.append("## E2 - Convergence after a three-way partition")
    table(res["E2_convergence"], list(res["E2_convergence"][0].keys()))
    L.append("## E3 - Randomized partition chaos")
    L.append("```json\n" + json.dumps({k: v for k, v in res["E3_chaos"].items() if k != "details"}, indent=2) + "\n```\n")
    L.append("## E4 - Allocation safety under partitions")
    L.append("```json\n" + json.dumps(res["E4_allocation"], indent=2) + "\n```\n")
    L.append("## E5 - Duplicate delivery")
    L.append("```json\n" + json.dumps(res["E5_duplicates"], indent=2) + "\n```\n")
    L.append("## E6 - Lossy links (retry and backoff)")
    table(res["E6_lossy"], list(res["E6_lossy"][0].keys()))
    L.append("## E7 - Projection rebuild cost")
    table(res["E7_rebuild"], list(res["E7_rebuild"][0].keys()))
    L.append("## E8 - Real processes over HTTP")
    L.append("```json\n" + json.dumps(res["E8_processes"], indent=2) + "\n```\n")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    trials = 2 if a.quick else 5
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "machine": f"{platform.system()} {platform.release()}, Python {platform.python_version()}, {platform.processor() or platform.machine()}"}
    for key, fn in [("E1_payload", e1_payload), ("E2_convergence", lambda: e2_convergence(trials)),
                    ("E3_chaos", lambda: e3_chaos(10 if a.quick else 25)), ("E4_allocation", lambda: e4_allocation(300)),
                    ("E5_duplicates", e5_duplicates), ("E6_lossy", lambda: e6_lossy(trials)), ("E7_rebuild", e7_rebuild),
                    ("E8_processes", e8_processes)]:
        t0 = time.perf_counter()
        res[key] = fn()
        print(f"{key} done in {time.perf_counter() - t0:.1f}s", flush=True)
    out = ROOT / "docs" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark_results.json").write_text(json.dumps(res, indent=2))
    (out / "benchmark_results.md").write_text(to_md(res))
    print(to_md(res))


if __name__ == "__main__":
    main()
