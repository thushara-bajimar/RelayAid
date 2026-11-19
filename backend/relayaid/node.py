"""A RelayAid node: one independent replica with its own database, identity and clocks.

All coordination operations are *local*: they validate against the node's own replica,
append a signed event to its own log and rebuild its own projection. Nothing here talks to
the network - synchronization (sync.py) only moves events between logs.
"""
from __future__ import annotations

import bisect
import json
import logging
import threading
import time
from typing import Any

from . import crypto
from . import events as E
from .config import NodeConfig
from .db import PROJECTION_TABLES, Database
from .netsim import NetSim
from .projection import Ev, Projection, happened_before
from .security import SessionStore

log = logging.getLogger("relayaid")

SENSITIVE_PERSON_FIELDS = ("name", "age", "description", "last_known_location", "contact")


class OpError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Node:
    def __init__(self, cfg: NodeConfig):
        self.cfg = cfg
        self.node_id = cfg.node_id
        self.db = Database(cfg.db_path)
        self.signer = crypto.Signer(cfg.signing_key)
        self.roster_keys = {nid: p.public_key for nid, p in cfg.roster.items()}
        self.sim = NetSim(self.db)
        self.sessions = SessionStore(cfg)
        self.lock = threading.RLock()
        self.last_rebuild_ms = 0.0
        self.started_at = time.time()
        self._load()

    # ================================================================== event log
    def _load(self) -> None:
        rows = self.db.query("SELECT * FROM events ORDER BY lamport, origin, seq")
        self._envelopes: dict[str, dict] = {}
        self._events: list[Ev] = []
        self._keys: list[tuple] = []
        self._seqs: dict[str, set[int]] = {}
        self.lamport = 0
        for r in rows:
            env = {k: r[k] for k in E.ENVELOPE_KEYS}
            env["vv"], env["payload"] = json.loads(env["vv"]), json.loads(env["payload"])
            self._index(env)
        self._recompute_vv()
        self.rebuild()

    def _index(self, env: dict) -> None:
        ev = Ev.from_row(env)
        self._envelopes[ev.event_id] = env
        pos = bisect.bisect(self._keys, ev.order_key)
        self._keys.insert(pos, ev.order_key)
        self._events.insert(pos, ev)
        self._seqs.setdefault(ev.origin, set()).add(ev.seq)
        self.lamport = max(self.lamport, ev.lamport)

    def _recompute_vv(self) -> None:
        vv = {}
        for origin, seqs in self._seqs.items():
            n = 0
            while n + 1 in seqs:
                n += 1
            if n:
                vv[origin] = n
        self.vv = dict(sorted(vv.items()))

    def version_vector(self) -> dict[str, int]:
        with self.lock:
            return dict(self.vv)

    def event_count(self) -> int:
        return len(self._events)

    def _insert(self, conn, env: dict, received_from: str) -> None:
        conn.execute(
            "INSERT INTO events (event_id, origin, seq, lamport, vv, entity_type, entity_id, type, payload,"
            " created_at, schema, sig, received_at, received_from) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (env["event_id"], env["origin"], env["seq"], env["lamport"], json.dumps(env["vv"], sort_keys=True),
             env["entity_type"], env["entity_id"], env["type"], json.dumps(env["payload"], sort_keys=True),
             env["created_at"], env["schema"], env["sig"], E.utcnow(), received_from))

    def rebuild(self) -> None:
        t0 = time.perf_counter()
        proj = Projection(self._events)
        with self.db.transaction() as conn:
            proj.write(conn)
        self.projection = proj
        self.last_rebuild_ms = (time.perf_counter() - t0) * 1000

    def append_local(self, entity_type: str, entity_id: str, event_type: str, payload: dict,
                     actor: str = "system") -> dict:
        """Create, sign, store and apply one local event. Works with no network at all."""
        with self.lock:
            seq = self.vv.get(self.node_id, 0) + 1
            if seq in self._seqs.get(self.node_id, set()):  # recovered events beyond a gap
                seq = max(self._seqs[self.node_id]) + 1
            vv = dict(self.vv)
            vv[self.node_id] = seq
            try:
                env = E.build_event(origin=self.node_id, seq=seq, lamport=self.lamport + 1, vv=vv,
                                    entity_type=entity_type, entity_id=entity_id, event_type=event_type,
                                    payload=payload, signer=self.signer)
            except E.EventError as exc:
                raise OpError(str(exc), 422) from exc
            with self.db.transaction() as conn:
                self._insert(conn, env, "local")
            self._index(env)
            self._recompute_vv()
            self.rebuild()
            if env["event_id"] in self.projection.rejected:  # local checks must mirror the projection
                log.error("local event %s rejected by projection: %s", env["event_id"],
                          self.projection.rejected[env["event_id"]]["reason"])
            log.info("local event %s %s %s", env["type"], entity_type, entity_id)  # ids only, no payload
            return env

    def ingest(self, raw_events: list, received_from: str) -> dict:
        """Idempotently store events received from a peer. Returns an acknowledgement."""
        accepted, duplicates, rejected = [], 0, []
        with self.lock:
            fresh = []
            for raw in raw_events:
                eid = raw.get("event_id") if isinstance(raw, dict) else None
                if eid in self._envelopes or any(f["event_id"] == eid for f in fresh):
                    duplicates += 1
                    continue
                try:
                    env = E.verify_event(raw, self.roster_keys)
                except E.EventError as exc:
                    rejected.append({"event_id": eid, "reason": str(exc)})
                    continue
                if env["seq"] in self._seqs.get(env["origin"], set()) or any(
                        f["origin"] == env["origin"] and f["seq"] == env["seq"] for f in fresh):
                    rejected.append({"event_id": eid, "reason": "equivocation: (origin, seq) already used"})
                    continue
                fresh.append(env)
            if fresh:
                with self.db.transaction() as conn:
                    for env in fresh:
                        self._insert(conn, env, received_from)
                for env in fresh:
                    self._index(env)
                    accepted.append(env["event_id"])
                self._recompute_vv()
                self.rebuild()
            if duplicates:
                self.db.bump("duplicate_events_ignored", duplicates)
            if rejected:
                self.db.bump("invalid_events_rejected", len(rejected))
                self.audit(received_from, "events_rejected", f"{len(rejected)} invalid event(s): "
                           + "; ".join(sorted({r['reason'] for r in rejected}))[:300])
            if accepted:
                self.db.bump("events_received", len(accepted))
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "vv": self.version_vector()}

    def missing_for(self, peer_vv: dict, cursor: list | None, limit: int) -> tuple[list[dict], list | None, bool]:
        """Events the peer lacks, in causal (Lamport) order, paginated by cursor."""
        with self.lock:
            start = bisect.bisect_right(self._keys, tuple(cursor)) if cursor else 0
            out, last = [], None
            for ev in self._events[start:]:
                if ev.seq > peer_vv.get(ev.origin, 0):
                    if len(out) >= limit:
                        return out, list(last), True
                    out.append(self._envelopes[ev.event_id])
                last = ev.order_key
            return out, (list(last) if last else cursor), False

    def pending_for(self, peer_vv: dict) -> int:
        with self.lock:
            return sum(1 for ev in self._events if ev.seq > peer_vv.get(ev.origin, 0))

    # ================================================================== bookkeeping
    def audit(self, actor: str, action: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
                        (E.utcnow(), actor, action, detail[:500]))

    def state_digest(self) -> dict:
        """Hash of the replicated logical state. Equal digests == converged replicas."""
        with self.lock:
            tables = {}
            for t in sorted(PROJECTION_TABLES):
                rows = self.db.query(f"SELECT * FROM {t}")
                tables[t] = sorted(rows, key=lambda r: crypto.canonical(r))
            ids = sorted(self._envelopes)
            return {"state": crypto.sha256_hex(crypto.canonical(tables))[:16],
                    "events": crypto.sha256_hex(crypto.canonical(ids))[:16],
                    "event_count": len(ids), "vv": dict(self.vv)}

    def _require_field_node(self) -> None:
        if self.cfg.is_server:
            raise OpError("the central server stores and relays events; field records are created on nodes", 403)

    # ================================================================== reads
    def _rows(self, sql: str, params=(), json_cols=("disputed_fields",)) -> list[dict]:
        rows = self.db.query(sql, params)
        for r in rows:
            for c in json_cols:
                if c in r and isinstance(r[c], str):
                    r[c] = json.loads(r[c])
        return rows

    def list_teams(self):
        return self._rows("SELECT * FROM teams ORDER BY name")

    def list_needs(self):
        rows = self._rows("SELECT * FROM needs ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                          "WHEN 'medium' THEN 2 ELSE 3 END, created_at")
        notes = self._group("SELECT * FROM need_notes ORDER BY at", "need_id")
        for r in rows:
            r["notes"] = notes.get(r["need_id"], [])
        return rows

    def list_shelters(self):
        rows = self._rows("SELECT * FROM shelters ORDER BY name")
        ups = self._group("SELECT * FROM shelter_updates ORDER BY at", "shelter_id")
        for r in rows:
            r["updates"] = ups.get(r["shelter_id"], [])[-25:]
        return rows

    def list_tasks(self):
        rows = self._rows("SELECT * FROM tasks ORDER BY created_at")
        notes = self._group("SELECT * FROM task_notes ORDER BY at", "task_id")
        for r in rows:
            r["notes"] = notes.get(r["task_id"], [])
        return rows

    def list_supplies(self):
        rows = self._rows("SELECT * FROM supplies ORDER BY kind, name")
        rights = self._group("SELECT * FROM allocation_rights ORDER BY node_id", "supply_id")
        allocs = self._group("SELECT * FROM resource_allocations ORDER BY created_at", "supply_id")
        transfers = self._group("SELECT * FROM rights_transfers ORDER BY at", "supply_id")
        reqs = self._group("SELECT * FROM rights_requests ORDER BY at", "supply_id")
        for r in rows:
            sid = r["supply_id"]
            r["rights"] = {x["node_id"]: x["balance"] for x in rights.get(sid, [])}
            r["my_rights"] = r["rights"].get(self.node_id, 0) if r["kind"] == "quantity" else (
                1 if r["holder"] == self.node_id else 0)
            r["allocations"] = allocs.get(sid, [])
            r["transfers"] = transfers.get(sid, [])
            r["requests"] = reqs.get(sid, [])
        return rows

    def _group(self, sql: str, key: str) -> dict[str, list]:
        out: dict[str, list] = {}
        for r in self.db.query(sql):
            out.setdefault(r[key], []).append(r)
        return out

    def list_conflicts(self, status: str | None = None):
        rows = self._rows("SELECT * FROM conflicts" + (" WHERE status = ?" if status else "")
                          + " ORDER BY detected_at DESC", (status,) if status else (),
                          json_cols=("candidates", "current_value"))
        for r in rows:  # never surface person ciphertext/values beyond status
            r["entity_label"] = self._label(r["entity_type"], r["entity_id"])
        return rows

    def _label(self, etype: str, eid: str) -> str:
        table = {"need": ("needs", "need_id", "description"), "shelter": ("shelters", "shelter_id", "name"),
                 "task": ("tasks", "task_id", "title"), "team": ("teams", "team_id", "name"),
                 "supply": ("supplies", "supply_id", "name")}.get(etype)
        if etype == "person":
            return f"Person record {eid[-6:]}"
        if not table:
            return eid
        row = self.db.one(f"SELECT {table[2]} AS l FROM {table[0]} WHERE {table[1]} = ?", (eid,))
        return row["l"] if row else eid

    def history(self, entity_type: str, entity_id: str) -> list[dict]:
        with self.lock:
            evs = [self._envelopes[e.event_id] for e in self._events
                   if e.entity_id == entity_id and e.entity_type == entity_type]
        rejected = self.projection.rejected
        out = []
        for env in evs:
            payload = env["payload"]
            if entity_type == "person":
                payload = _redact(payload)
            out.append({"event_id": env["event_id"], "origin": env["origin"], "seq": env["seq"],
                        "lamport": env["lamport"], "type": env["type"], "created_at": env["created_at"],
                        "payload": payload, "rejected": rejected.get(env["event_id"], {}).get("reason")})
        return out

    def recent_events(self, limit: int = 50) -> list[dict]:
        with self.lock:
            evs = [self._envelopes[e.event_id] for e in self._events[-limit:]][::-1]
        return [{"event_id": e["event_id"], "origin": e["origin"], "seq": e["seq"], "lamport": e["lamport"],
                 "type": e["type"], "entity_type": e["entity_type"], "entity_id": e["entity_id"],
                 "created_at": e["created_at"], "vv": e["vv"],
                 "payload": _redact(e["payload"]) if e["entity_type"] == "person" else e["payload"]}
                for e in evs]

    # ------------------------------------------------------------------ people (protected)
    def list_people(self, dek: bytes | None, detailed: bool) -> list[dict]:
        rows = self.db.query("SELECT * FROM people ORDER BY updated_at DESC")
        evidence = self._group("SELECT * FROM people_evidence ORDER BY at", "person_id")
        out = []
        for r in rows:
            rec = {k: r[k] for k in ("person_id", "status", "status_event_id", "verification", "reporting_team",
                                     "origin", "created_at", "updated_at")}
            rec["evidence_count"] = len(evidence.get(r["person_id"], []))
            if detailed and dek:
                rec["details"] = self._decrypt_all(dek, r["person_id"], json.loads(r["sensitive"]))
                rec["evidence"] = [{"note_id": n["note_id"], "origin": n["origin"], "at": n["at"],
                                    "text": self._decrypt(dek, n["ciphertext"], f"person:{r['person_id']}:evidence")}
                                   for n in evidence.get(r["person_id"], [])]
            else:
                rec["details"] = None  # redacted: role lacks permission or node is locked
            out.append(rec)
        return out

    def _decrypt(self, dek: bytes, token: str, aad: str) -> str:
        try:
            return crypto.decrypt_field(dek, token, aad)
        except ValueError:
            return "[unreadable: integrity check failed]"

    def _decrypt_all(self, dek: bytes, pid: str, sens: dict) -> dict:
        return {k: self._decrypt(dek, v, f"person:{pid}:{k}") for k, v in sens.items()}

    # ================================================================== writes: teams/needs
    def _exists(self, table: str, key: str, eid: str) -> dict:
        row = self.db.one(f"SELECT * FROM {table} WHERE {key} = ?", (eid,))
        if row is None:
            raise OpError(f"{table[:-1]} {eid} not found on this node", 404)
        return row

    def _guard_disputed(self, row: dict, fields: dict, critical: set[str]) -> None:
        disputed = set(json.loads(row.get("disputed_fields") or "[]"))
        blocked = sorted(disputed & critical & {k for k, v in fields.items() if v is not None})
        if blocked:
            raise OpError(f"field(s) {', '.join(blocked)} are disputed by concurrent updates; "
                          "resolve the conflict in the Conflict Center first", 409)

    def register_team(self, data: dict, actor: str) -> dict:
        self._require_field_node()
        tid = E.new_id("tm_")
        self.append_local("team", tid, "team.registered", {**data, "home_node": self.node_id}, actor)
        return {"team_id": tid}

    def create_need(self, data: dict, actor: str) -> dict:
        self._require_field_node()
        nid = E.new_id("nd_")
        self.append_local("need", nid, "need.created", data, actor)
        return {"need_id": nid}

    def update_need(self, nid: str, fields: dict, actor: str) -> dict:
        self._require_field_node()
        row = self._exists("needs", "need_id", nid)
        self._guard_disputed(row, fields, {"status"})
        return self.append_local("need", nid, "need.updated", {"fields": fields}, actor)

    def add_note(self, entity_type: str, eid: str, text: str, actor: str) -> dict:
        self._require_field_node()
        table, key = {"need": ("needs", "need_id"), "task": ("tasks", "task_id"),
                      "shelter": ("shelters", "shelter_id")}[entity_type]
        self._exists(table, key, eid)
        return self.append_local(entity_type, eid, f"{entity_type}.note_added",
                                 {"note_id": E.new_id("nt_"), "text": text}, actor)

    # ================================================================== writes: shelters
    def register_shelter(self, data: dict, actor: str) -> dict:
        self._require_field_node()
        sid = E.new_id("sh_")
        self.append_local("shelter", sid, "shelter.registered", data, actor)
        return {"shelter_id": sid}

    def update_shelter(self, sid: str, fields: dict, actor: str) -> dict:
        self._require_field_node()
        row = self._exists("shelters", "shelter_id", sid)
        self._guard_disputed(row, fields, {"capacity", "status"})
        return self.append_local("shelter", sid, "shelter.updated", {"fields": fields}, actor)

    def change_occupancy(self, sid: str, delta: int, reason: str, actor: str) -> dict:
        self._require_field_node()
        row = self._exists("shelters", "shelter_id", sid)
        if row["occupancy"] + delta < 0:
            raise OpError("occupancy cannot go below zero on this replica", 409)
        env = self.append_local("shelter", sid, "shelter.occupancy_changed", {"delta": delta, "reason": reason}, actor)
        new = self.db.one("SELECT occupancy, capacity FROM shelters WHERE shelter_id = ?", (sid,))
        return {"event": env["event_id"], "occupancy": new["occupancy"], "capacity": new["capacity"],
                "over_capacity": new["occupancy"] > new["capacity"]}

    # ================================================================== writes: tasks
    def create_task(self, data: dict, actor: str) -> dict:
        self._require_field_node()
        tid = E.new_id("tk_")
        self.append_local("task", tid, "task.created", data, actor)
        return {"task_id": tid}

    def update_task(self, tid: str, fields: dict, actor: str) -> dict:
        self._require_field_node()
        self._exists("tasks", "task_id", tid)
        return self.append_local("task", tid, "task.updated", {"fields": fields}, actor)

    def assign_task(self, tid: str, team_id: str | None, actor: str) -> dict:
        self._require_field_node()
        row = self._exists("tasks", "task_id", tid)
        self._guard_disputed(row, {"assigned_team": team_id or "unassign"}, {"assigned_team"})
        if team_id:
            self._exists("teams", "team_id", team_id)
        env = self.append_local("task", tid, "task.assigned", {"team_id": team_id}, actor)
        if team_id and row["status"] == "open":
            self.append_local("task", tid, "task.status_changed", {"status": "assigned", "note": ""}, actor)
        return env

    def set_task_status(self, tid: str, status: str, note: str, actor: str) -> dict:
        self._require_field_node()
        row = self._exists("tasks", "task_id", tid)
        self._guard_disputed(row, {"status": status}, {"status"})
        return self.append_local("task", tid, "task.status_changed", {"status": status, "note": note}, actor)

    # ================================================================== writes: people (sensitive)
    def report_person(self, data: dict, dek: bytes | None, actor: str) -> dict:
        self._require_field_node()
        if dek is None:
            raise OpError("this session cannot encrypt person records (no data key)", 403)
        pid = E.new_id("pr_")
        sensitive = {k: crypto.encrypt_field(dek, str(data[k]), f"person:{pid}:{k}")
                     for k in SENSITIVE_PERSON_FIELDS if data.get(k) not in (None, "")}
        self.append_local("person", pid, "person.reported", {
            "status": data.get("status", "missing"), "reporting_team": data.get("reporting_team", ""),
            "sensitive": sensitive}, actor)
        self.audit(actor, "person_reported", pid)
        return {"person_id": pid}

    def update_person_status(self, pid: str, status: str, evidence: str | None, dek: bytes | None,
                             actor: str) -> dict:
        self._require_field_node()
        row = self._exists("people", "person_id", pid)
        if row["verification"] == "disputed":
            raise OpError("this record has conflicting status reports; resolve it in the Conflict Center", 409)
        if evidence and dek is None:
            raise OpError("this session cannot encrypt evidence", 403)
        ct = crypto.encrypt_field(dek, evidence, f"person:{pid}:evidence") if evidence else None
        env = self.append_local("person", pid, "person.status_updated", {"status": status, "evidence": ct}, actor)
        self.audit(actor, "person_status_updated", pid)
        return env

    def verify_person(self, pid: str, actor: str) -> dict:
        self._require_field_node()
        row = self._exists("people", "person_id", pid)
        if row["verification"] == "disputed":
            raise OpError("cannot verify a disputed status; resolve the conflict first", 409)
        env = self.append_local("person", pid, "person.verified", {"status_event_id": row["status_event_id"]}, actor)
        self.audit(actor, "person_verified", pid)
        return env

    def add_person_evidence(self, pid: str, text: str, dek: bytes | None, actor: str) -> dict:
        self._require_field_node()
        self._exists("people", "person_id", pid)
        if dek is None:
            raise OpError("this session cannot encrypt evidence", 403)
        note_id = E.new_id("ev_")
        return self.append_local("person", pid, "person.evidence_added", {
            "note_id": note_id, "ciphertext": crypto.encrypt_field(dek, text, f"person:{pid}:evidence")}, actor)

    # ================================================================== writes: supplies & escrow
    def register_supply(self, data: dict, actor: str) -> dict:
        self._require_field_node()
        sid = E.new_id("sp_") if not data.get("supply_id") else data.pop("supply_id")
        if not E.ID_RE.match(sid):
            raise OpError("invalid supply id")
        if self.db.one("SELECT 1 FROM supplies WHERE supply_id = ?", (sid,)):
            raise OpError(f"supply {sid} already exists", 409)
        self.append_local("supply", sid, "supply.registered", data, actor)
        return {"supply_id": sid}

    def update_supply(self, sid: str, fields: dict, actor: str) -> dict:
        self._require_field_node()
        self._exists("supplies", "supply_id", sid)
        return self.append_local("supply", sid, "supply.updated", {"fields": fields}, actor)

    def _blocked(self, actor: str, sid: str, reason: str):
        self.db.bump("allocations_blocked")
        self.audit(actor, "allocation_blocked", f"{sid}: {reason}")
        log.info("allocation blocked on %s for %s", self.node_id, sid)
        return OpError(reason, 409)

    def _supply(self, sid: str):
        self._exists("supplies", "supply_id", sid)
        return self.projection.supplies[sid]

    def _my_rights(self, ent) -> int:
        probe = Ev("probe", self.node_id, self.vv.get(self.node_id, 0) + 1, self.lamport + 1,
                   {**self.vv, self.node_id: self.vv.get(self.node_id, 0) + 1}, "supply", ent.eid, "probe", {}, "")
        return Projection.rights_known(ent, self.node_id, probe)

    def allocate(self, sid: str, amount: int, target_type: str, target_id: str | None, note: str, actor: str) -> dict:
        self._require_field_node()
        with self.lock:
            ent = self._supply(sid)
            x = ent.extra
            payload = {"allocation_id": E.new_id("al_"), "amount": amount, "target_type": target_type,
                       "target_id": target_id, "note": note, "epoch": None}
            if x["kind"] == "indivisible":
                if amount != 1:
                    raise self._blocked(actor, sid, "indivisible resources are allocated as one unit")
                if x["holder"] != self.node_id:
                    raise self._blocked(actor, sid, (
                        f"{self.node_id} does not hold the exclusive allocation authority for this resource "
                        f"(held by {x['holder']}, epoch {x['epoch']}). Request a transfer from {x['holder']}; "
                        "offline allocation without the token is refused to prevent double allocation."))
                if any(a["status"] == "reserved" for a in x["allocations"].values()):
                    raise self._blocked(actor, sid, "resource is already allocated; release it first")
                if not x["in_service"]:
                    raise self._blocked(actor, sid, "resource is marked unavailable")
                payload["epoch"] = x["epoch"]
            else:
                have = self._my_rights(ent)
                if have < amount:
                    raise self._blocked(actor, sid, (
                        f"{self.node_id} holds allocation rights for {have} {x['unit']} but {amount} were requested. "
                        "Request more rights from another node; rights cannot be created offline."))
            env = self.append_local("supply", sid, "supply.allocated", payload, actor)
            self.audit(actor, "resource_allocated", f"{sid} x{amount} -> {target_type}:{target_id or '-'}")
            return {"allocation_id": payload["allocation_id"], "event_id": env["event_id"]}

    def transfer_rights(self, sid: str, to_node: str, amount: int, request_id: str | None, actor: str) -> dict:
        self._require_field_node()
        peer = self.cfg.roster.get(to_node)
        if peer is None or peer.role != "node" or to_node == self.node_id:
            raise OpError("rights can only be transferred to another field node of this deployment")
        with self.lock:
            ent = self._supply(sid)
            x = ent.extra
            tid = E.new_id("tr_")
            if x["kind"] == "indivisible":
                if x["holder"] != self.node_id:
                    raise self._blocked(actor, sid, f"only the token holder ({x['holder']}) can transfer authority")
                if any(a["status"] == "reserved" for a in x["allocations"].values()):
                    raise self._blocked(actor, sid, "release the current allocation before transferring authority")
                env = self.append_local("supply", sid, "supply.token_transferred", {
                    "transfer_id": tid, "to_node": to_node, "epoch": x["epoch"], "request_id": request_id}, actor)
            else:
                have = self._my_rights(ent)
                if have < amount:
                    raise self._blocked(actor, sid, f"cannot transfer {amount}: only {have} rights held")
                env = self.append_local("supply", sid, "supply.rights_transferred", {
                    "transfer_id": tid, "to_node": to_node, "amount": amount, "request_id": request_id}, actor)
            self.audit(actor, "rights_transferred", f"{sid} -> {to_node} ({amount})")
            return {"transfer_id": tid, "event_id": env["event_id"]}

    def _allocation(self, aid: str) -> tuple[str, dict]:
        row = self.db.one("SELECT * FROM resource_allocations WHERE allocation_id = ?", (aid,))
        if row is None:
            raise OpError("allocation not found", 404)
        if row["status"] != "reserved":
            raise OpError(f"allocation is already {row['status']}", 409)
        if row["node_id"] != self.node_id:
            raise OpError(f"only {row['node_id']} (the allocating node) can change this allocation", 403)
        return row["supply_id"], row

    def release_allocation(self, aid: str, actor: str) -> dict:
        self._require_field_node()
        with self.lock:
            sid, _ = self._allocation(aid)
            return self.append_local("supply", sid, "supply.allocation_released", {"allocation_id": aid}, actor)

    def distribute_allocation(self, aid: str, actor: str) -> dict:
        self._require_field_node()
        with self.lock:
            sid, _ = self._allocation(aid)
            if self.projection.supplies[sid].extra["kind"] != "quantity":
                raise OpError("indivisible resources are released, not distributed")
            return self.append_local("supply", sid, "supply.distributed", {"allocation_id": aid}, actor)

    def restock(self, sid: str, amount: int, note: str, actor: str) -> dict:
        self._require_field_node()
        if self._supply(sid).extra["kind"] != "quantity":
            raise OpError("indivisible resources cannot be restocked; register a new unit instead")
        return self.append_local("supply", sid, "supply.restocked", {"amount": amount, "note": note}, actor)

    def write_off(self, sid: str, amount: int, reason: str, actor: str) -> dict:
        self._require_field_node()
        with self.lock:
            ent = self._supply(sid)
            if ent.extra["kind"] != "quantity":
                raise OpError("use availability changes for indivisible resources")
            if self._my_rights(ent) < amount:
                raise self._blocked(actor, sid, "cannot write off more than this node's rights")
            return self.append_local("supply", sid, "supply.written_off", {"amount": amount, "reason": reason}, actor)

    def set_availability(self, sid: str, available: bool, reason: str, actor: str) -> dict:
        self._require_field_node()
        with self.lock:
            x = self._supply(sid).extra
            if x["kind"] != "indivisible" or x["holder"] != self.node_id:
                raise self._blocked(actor, sid, "only the token holder can change availability")
            return self.append_local("supply", sid, "supply.availability_changed",
                                     {"available": available, "reason": reason}, actor)

    def request_rights(self, sid: str, amount: int, reason: str, actor: str) -> dict:
        self._require_field_node()
        self._supply(sid)
        rid = E.new_id("rq_")
        self.append_local("supply", sid, "supply.rights_requested", {"request_id": rid, "amount": amount,
                                                                      "reason": reason}, actor)
        return {"request_id": rid}

    def decline_request(self, sid: str, rid: str, reason: str, actor: str) -> dict:
        self._require_field_node()
        self._supply(sid)
        return self.append_local("supply", sid, "supply.request_declined", {"request_id": rid, "reason": reason}, actor)

    def verify_allocations(self) -> dict:
        """Audit allocation invariants on this replica (used by tests and the dashboard)."""
        problems = []
        for sid, ent in self.projection.supplies.items():
            x = ent.extra
            active = [a for a in x["allocations"].values() if a["status"] == "reserved"]
            if x["kind"] == "indivisible" and len(active) > 1:
                problems.append(f"{sid}: {len(active)} concurrent allocations of an indivisible resource")
            if x["kind"] == "quantity":
                bal = self.projection.supply_balances(ent)
                if any(b < 0 for b in bal.values()):
                    problems.append(f"{sid}: negative rights balance {bal}")
                if sum(bal.values()) + x["reserved"] + x["distributed"] + x["unavailable"] != x["total"]:
                    problems.append(f"{sid}: quantity accounting does not balance")
        rejected = [r for r in self.projection.rejected.values() if r["type"].startswith("supply.")]
        return {"ok": not problems, "problems": problems, "supplies_checked": len(self.projection.supplies),
                "invalid_allocation_events_rejected": len(rejected), "rejected": rejected}

    # ================================================================== conflicts
    def resolve_conflict(self, cid: str, value: Any, rationale: str, actor: str) -> dict:
        self._require_field_node()
        c = self.db.one("SELECT * FROM conflicts WHERE conflict_id = ?", (cid,))
        if c is None:
            raise OpError("conflict not found", 404)
        if c["kind"] != "concurrent_write":
            raise OpError("invariant conflicts are acknowledged, not resolved by choosing a value")
        if c["status"] != "open":
            raise OpError(f"conflict is already {c['status']}", 409)
        if not Projection._valid_field_value(c["entity_type"], c["field"], value):
            raise OpError("value is not valid for this field", 422)
        env = self.append_local(c["entity_type"], c["entity_id"], "conflict.resolved", {
            "conflict_id": cid, "field": c["field"], "value": value, "rationale": rationale}, actor)
        self.audit(actor, "conflict_resolved", cid)
        return env

    def acknowledge_conflict(self, cid: str, note: str, actor: str) -> dict:
        self._require_field_node()
        c = self.db.one("SELECT * FROM conflicts WHERE conflict_id = ?", (cid,))
        if c is None or c["kind"] != "invariant":
            raise OpError("invariant conflict not found", 404)
        env = self.append_local(c["entity_type"], c["entity_id"], "conflict.acknowledged",
                                {"conflict_id": cid, "note": note}, actor)
        self.audit(actor, "conflict_acknowledged", cid)
        return env

    # ================================================================== status
    def overview(self) -> dict:
        q = lambda sql: self.db.one(sql)["n"]  # noqa: E731
        return {
            "active_needs": q("SELECT COUNT(*) n FROM needs WHERE status IN ('open','in_progress')"),
            "critical_needs": q("SELECT COUNT(*) n FROM needs WHERE status IN ('open','in_progress') AND priority='critical'"),
            "supplies": q("SELECT COUNT(*) n FROM supplies"),
            "supplies_available": q("SELECT COUNT(*) n FROM supplies WHERE status='available'"),
            "shelters_open": q("SELECT COUNT(*) n FROM shelters WHERE status='open'"),
            "shelter_space": q("SELECT COALESCE(SUM(available_space),0) n FROM shelters WHERE status != 'closed'"),
            "active_tasks": q("SELECT COUNT(*) n FROM tasks WHERE status NOT IN ('completed','cancelled')"),
            "people_alerts": q("SELECT COUNT(*) n FROM people WHERE status IN ('missing','needs_assistance','unknown')"),
            "people_unverified": q("SELECT COUNT(*) n FROM people WHERE verification != 'verified'"),
            "open_conflicts": q("SELECT COUNT(*) n FROM conflicts WHERE status='open'"),
            "pending_requests": q("SELECT COUNT(*) n FROM rights_requests WHERE status='pending'"),
            "teams": q("SELECT COUNT(*) n FROM teams"),
        }


def _redact(payload: dict) -> dict:
    def walk(v):
        if crypto.is_ciphertext(v):
            return "[encrypted]"
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    return walk(payload)
