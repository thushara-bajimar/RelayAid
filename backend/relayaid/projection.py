"""Deterministic projection: event set -> materialized state.

Why a full deterministic fold?
    Every replica stores a *set* of immutable events. The projection sorts that set by
    (lamport, origin, seq) - a total order that is consistent with causality because a
    Lamport clock always increases along happened-before edges - and folds it through pure
    merge rules. Two replicas holding the same event set therefore compute byte-identical
    state, no matter in which order or how many times the events arrived. That is the
    convergence argument, and tests compare the resulting state digests directly.

Merge policies (per field)
    lww       last-writer-wins register on (lamport, origin). Concurrent different values are
              merged automatically but *recorded* as an `auto_merged` conflict for transparency.
    lattice   join-semilattice register (value with the highest rank wins). Used for task
              status (open < assigned < in_progress < completed) and priority (max wins).
              Two different values with the same top rank (completed vs cancelled) are a
              real conflict and stay `open`.
    critical  multi-value register. Concurrent different values are NOT auto-resolved: the
              field is marked disputed, all candidates are preserved, and a conflict stays
              `open` until a later write that causally dominates every candidate (an explicit
              `conflict.resolved` event or a deliberate update by someone who saw them all).
    counter   shelter occupancy is a PN-counter of signed deltas; concurrent arrivals both
              count. Capacity violations are surfaced as `invariant` conflicts.
    g-set     notes and evidence are grow-only sets keyed by id: nothing is ever lost.

Allocation safety is validated here as well (escrow rights / exclusive tokens): an event
that spends rights its origin did not causally hold is *rejected* and recorded - it never
affects state on any replica.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import crypto, events as ev_mod

PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
TASK_STATUS_RANK = {"open": 0, "assigned": 1, "in_progress": 2, "completed": 3, "cancelled": 3}

NEED_POLICY = {"category": "lww", "description": "lww", "quantity": "lww", "unit": "lww",
               "priority": ("lattice", PRIORITY_RANK), "location": "lww", "responsible_team": "lww",
               "status": "critical"}
SHELTER_POLICY = {"name": "lww", "location": "lww", "accessibility": "lww", "conditions": "lww",
                  "capacity": "critical", "status": "critical"}
TASK_POLICY = {"title": "lww", "description": "lww", "location": "lww",
               "priority": ("lattice", PRIORITY_RANK), "assigned_team": "critical",
               "status": ("lattice", TASK_STATUS_RANK)}
TEAM_POLICY = {"name": "lww", "specialty": "lww", "members": "lww"}
SUPPLY_POLICY = {"name": "lww", "category": "lww", "location": "lww", "condition": "lww"}
PERSON_POLICY = {"status": "critical", "reporting_team": "lww"}

POLICIES = {"need": NEED_POLICY, "shelter": SHELTER_POLICY, "task": TASK_POLICY, "team": TEAM_POLICY,
            "supply": SUPPLY_POLICY, "person": PERSON_POLICY}
FIELD_MODELS = {"need": ev_mod.NeedFields, "shelter": ev_mod.ShelterFields, "task": ev_mod.TaskFields,
                "team": ev_mod.TeamFields, "supply": ev_mod.SupplyFields}
EXTRA_FIELD_VALUES = {("task", "assigned_team"): lambda v: v is None or isinstance(v, str),
                      ("task", "status"): lambda v: v in TASK_STATUS_RANK,
                      ("person", "status"): lambda v: v in {"missing", "located", "found", "reunited",
                                                            "needs_assistance", "unknown"}}


@dataclass
class Ev:
    event_id: str
    origin: str
    seq: int
    lamport: int
    vv: dict
    entity_type: str
    entity_id: str
    type: str
    payload: dict
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "Ev":
        return cls(row["event_id"], row["origin"], row["seq"], row["lamport"],
                   row["vv"] if isinstance(row["vv"], dict) else json.loads(row["vv"]),
                   row["entity_type"], row["entity_id"], row["type"],
                   row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"]),
                   row["created_at"])

    @property
    def order_key(self):
        return (self.lamport, self.origin, self.seq)


def happened_before(a: Ev, b: Ev) -> bool:
    """True iff `a` is in the causal past of `b` (vector-clock comparison)."""
    return a.event_id != b.event_id and b.vv.get(a.origin, 0) >= a.seq


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True)


class Register:
    """A causally-aware register keeping its *frontier*: the writes not superseded by a
    causally later write. More than one frontier write == concurrent writes."""

    __slots__ = ("entity_type", "entity_id", "field", "policy", "rank", "frontier", "conflict")

    def __init__(self, entity_type: str, entity_id: str, fname: str, policy):
        self.entity_type, self.entity_id, self.field = entity_type, entity_id, fname
        if isinstance(policy, tuple):
            self.policy, self.rank = policy
        else:
            self.policy, self.rank = policy, None
        self.frontier: list[tuple[Ev, Any]] = []
        self.conflict: dict | None = None

    def _lww(self, writes):
        return max(writes, key=lambda w: w[0].order_key)

    def value(self):
        if not self.frontier:
            return None
        if self.policy == "lattice":
            top = max(self.rank.get(v, -1) for _, v in self.frontier)
            return self._lww([w for w in self.frontier if self.rank.get(w[1], -1) == top])[1]
        return self._lww(self.frontier)[1]

    def winner_event(self) -> Ev | None:
        if not self.frontier:
            return None
        val = self.value()
        return self._lww([w for w in self.frontier if _canon(w[1]) == _canon(val)])[0]

    def distinct(self) -> int:
        return len({_canon(v) for _, v in self.frontier})

    def disputed(self) -> bool:
        if self.distinct() <= 1:
            return False
        if self.policy == "critical":
            return True
        if self.policy == "lattice":
            top = max(self.rank.get(v, -1) for _, v in self.frontier)
            return len({_canon(v) for _, v in self.frontier if self.rank.get(v, -1) == top}) > 1
        return False

    def updated_at(self) -> str:
        return max(e.created_at for e, _ in self.frontier) if self.frontier else ""


class Entity:
    def __init__(self, etype: str, eid: str, ev: Ev):
        self.etype, self.eid = etype, eid
        self.origin, self.created_at, self.updated_at = ev.origin, ev.created_at, ev.created_at
        self.regs: dict[str, Register] = {
            f: Register(etype, eid, f, p) for f, p in POLICIES.get(etype, {}).items()}
        self.extra: dict[str, Any] = {}

    def get(self, f):
        return self.regs[f].value()

    def disputed_fields(self) -> list[str]:
        return sorted(f for f, r in self.regs.items() if r.disputed())


class Projection:
    def __init__(self, sorted_events: Iterable[Ev]):
        self.teams: dict[str, Entity] = {}
        self.needs: dict[str, Entity] = {}
        self.shelters: dict[str, Entity] = {}
        self.tasks: dict[str, Entity] = {}
        self.people: dict[str, Entity] = {}
        self.supplies: dict[str, Entity] = {}
        self.conflicts: dict[str, dict] = {}
        self.rejected: dict[str, dict] = {}
        self.awaiting_deps: list[str] = []
        self.applied = 0
        for ev in sorted_events:
            self.apply(ev)
        self._finish()

    # ------------------------------------------------------------------ helpers
    def _table(self, etype: str) -> dict[str, Entity] | None:
        return {"team": self.teams, "need": self.needs, "shelter": self.shelters, "task": self.tasks,
                "person": self.people, "supply": self.supplies}.get(etype)

    def _reject(self, ev: Ev, reason: str) -> None:
        self.rejected[ev.event_id] = {"event_id": ev.event_id, "origin": ev.origin, "type": ev.type,
                                      "reason": reason}

    def _write(self, ent: Entity, fname: str, ev: Ev, value: Any) -> None:
        reg = ent.regs[fname]
        reg.frontier = [(e, v) for e, v in reg.frontier if not happened_before(e, ev)]
        reg.frontier.append((ev, value))
        ent.updated_at = max(ent.updated_at, ev.created_at)
        self._track_conflict(reg, ev)

    def _track_conflict(self, reg: Register, ev: Ev) -> None:
        if reg.distinct() > 1:
            if reg.conflict is None:
                ids = sorted(e.event_id for e, _ in reg.frontier)
                cid = "cf_" + crypto.sha256_hex(
                    f"{reg.entity_type}|{reg.entity_id}|{reg.field}|{ids[0]}|{ids[1]}".encode())[:16]
                reg.conflict = self.conflicts[cid] = {
                    "conflict_id": cid, "kind": "concurrent_write", "entity_type": reg.entity_type,
                    "entity_id": reg.entity_id, "field": reg.field,
                    "policy": reg.policy, "status": "", "candidates": [], "current_value": None,
                    "resolution_kind": None, "resolved_by": None, "rationale": None,
                    "detected_at": ev.created_at, "resolved_at": None}
            rec = reg.conflict
            known = {c["event_id"] for c in rec["candidates"]}
            for e, v in reg.frontier:
                if e.event_id not in known:
                    rec["candidates"].append({"event_id": e.event_id, "origin": e.origin, "lamport": e.lamport,
                                              "created_at": e.created_at, "value": v, "event_type": e.type})
            rec["current_value"] = reg.value()
            if reg.disputed():
                rec["status"], rec["resolution_kind"] = "open", None
            elif rec["status"] != "open":
                rec["status"] = "auto_merged"
                rec["resolution_kind"] = "auto_lattice" if reg.policy == "lattice" else "auto_lww"
        elif reg.conflict is not None:
            rec = reg.conflict
            if rec["status"] == "open":
                rec["status"] = "resolved"
                rec["resolution_kind"] = "explicit" if ev.type == "conflict.resolved" else "superseded"
            rec["resolved_by"] = ev.event_id
            rec["resolved_at"] = ev.created_at
            rec["current_value"] = reg.value()
            if ev.type == "conflict.resolved":
                rec["rationale"] = ev.payload.get("rationale")
            reg.conflict = None

    def _write_fields(self, ent: Entity, ev: Ev, fields: dict) -> None:
        for fname, value in sorted(fields.items()):
            if value is not None and fname in ent.regs:
                self._write(ent, fname, ev, value)

    # ------------------------------------------------------------------ dispatcher
    def apply(self, ev: Ev) -> None:
        etype, eid, t, p = ev.entity_type, ev.entity_id, ev.type, ev.payload
        creators = {"team.registered", "need.created", "shelter.registered", "task.created",
                    "person.reported", "supply.registered"}
        table = self._table(etype)
        if t in creators:
            if eid in table:
                self._reject(ev, "entity already exists")
                return
            ent = table[eid] = Entity(etype, eid, ev)
            self._create(ent, ev)
            self.applied += 1
            return
        if table is None or eid not in table:
            self.awaiting_deps.append(ev.event_id)  # causal predecessor not received yet
            return
        ent = table[eid]
        handler = getattr(self, "_on_" + t.replace(".", "_"))
        handler(ent, ev, p)
        self.applied += 1

    def _create(self, ent: Entity, ev: Ev) -> None:
        p = ev.payload
        if ent.etype == "team":
            self._write_fields(ent, ev, {k: p[k] for k in ("name", "specialty", "members")})
            ent.extra["home_node"] = p["home_node"]
        elif ent.etype == "need":
            self._write_fields(ent, ev, {k: p[k] for k in NEED_POLICY if p.get(k) is not None})
            ent.extra["notes"] = {}
            if p.get("responsible_team") is None:
                ent.regs["responsible_team"].frontier.append((ev, None))
        elif ent.etype == "shelter":
            self._write_fields(ent, ev, {k: p[k] for k in SHELTER_POLICY})
            ent.extra.update(occupancy=0, updates=[], acks=[], invariant=None, notes={})
        elif ent.etype == "task":
            self._write_fields(ent, ev, {k: p[k] for k in ("title", "description", "priority", "location")})
            ent.regs["assigned_team"].frontier.append((ev, None))
            self._write(ent, "status", ev, "open")
            ent.extra.update(need_id=p.get("need_id"), notes={})
        elif ent.etype == "person":
            self._write(ent, "status", ev, p["status"])
            self._write(ent, "reporting_team", ev, p["reporting_team"])
            ent.extra.update(sensitive={k: v for k, v in p["sensitive"].items() if v is not None},
                             evidence={}, verified_refs=set())
        elif ent.etype == "supply":
            self._write_fields(ent, ev, {k: p[k] for k in SUPPLY_POLICY})
            qty = p["quantity"] if p["kind"] == "quantity" else 1
            ent.extra.update(kind=p["kind"], unit=p["unit"], total=qty, credits={}, debits={},
                             reserved=0, distributed=0, unavailable=0, allocations={}, transfers=[],
                             requests={}, holder=None, epoch=None, in_service=True)
            if p["kind"] == "quantity":
                self._credit(ent, ev.origin, ev, qty)
            else:
                ent.extra.update(holder=ev.origin, epoch=0)

    # ------------------------------------------------------------------ generic updates
    def _generic_update(self, ent: Entity, ev: Ev, p: dict) -> None:
        self._write_fields(ent, ev, p["fields"])

    _on_team_updated = _on_need_updated = _on_shelter_updated = _on_task_updated = _generic_update
    _on_supply_updated = _generic_update

    def _note(self, ent: Entity, ev: Ev, p: dict) -> None:
        ent.extra["notes"].setdefault(p["note_id"], {"note_id": p["note_id"], "text": p["text"],
                                                     "origin": ev.origin, "at": ev.created_at})
        ent.updated_at = max(ent.updated_at, ev.created_at)

    _on_need_note_added = _on_task_note_added = _note

    def _on_shelter_note_added(self, ent, ev, p):
        self._note(ent, ev, p)
        ent.extra["updates"].append({"event_id": ev.event_id, "kind": "note", "delta": None,
                                     "reason": p["text"], "origin": ev.origin, "at": ev.created_at})

    def _on_task_assigned(self, ent, ev, p):
        self._write(ent, "assigned_team", ev, p["team_id"])

    def _on_task_status_changed(self, ent, ev, p):
        self._write(ent, "status", ev, p["status"])

    # ------------------------------------------------------------------ shelters
    def _on_shelter_occupancy_changed(self, ent, ev, p):
        ent.extra["occupancy"] += p["delta"]
        ent.extra["updates"].append({"event_id": ev.event_id, "kind": "occupancy", "delta": p["delta"],
                                     "reason": p["reason"], "origin": ev.origin, "at": ev.created_at})
        ent.updated_at = max(ent.updated_at, ev.created_at)
        self._check_capacity(ent, ev)

    def _check_capacity(self, ent: Entity, ev: Ev) -> None:
        occ, cap = ent.extra["occupancy"], ent.get("capacity")
        violated = occ > cap or occ < 0
        inv = ent.extra["invariant"]
        if violated:
            if inv is None:
                cid = "iv_" + crypto.sha256_hex(f"{ent.eid}|occupancy|{ev.event_id}".encode())[:16]
                inv = ent.extra["invariant"] = self.conflicts[cid] = {
                    "conflict_id": cid, "kind": "invariant", "entity_type": "shelter", "entity_id": ent.eid,
                    "field": "occupancy", "policy": "invariant", "status": "open", "candidates": [],
                    "current_value": None, "resolution_kind": None, "resolved_by": None, "rationale": None,
                    "detected_at": ev.created_at, "resolved_at": None}
            elif inv["status"] == "acknowledged" and ev.type != "conflict.acknowledged":
                inv["status"] = "open"  # new changes after the acknowledgement need a fresh look
            inv["current_value"] = {"occupancy": occ, "capacity": cap}
            inv["candidates"].append({"event_id": ev.event_id, "origin": ev.origin, "lamport": ev.lamport,
                                      "created_at": ev.created_at, "event_type": ev.type,
                                      "value": {"occupancy": occ, "capacity": cap}})
        elif inv is not None:
            inv.update(status="resolved", resolution_kind="invariant_restored", resolved_by=ev.event_id,
                       resolved_at=ev.created_at, current_value={"occupancy": occ, "capacity": cap})
            ent.extra["invariant"] = None

    # ------------------------------------------------------------------ conflicts
    def _on_conflict_resolved(self, ent, ev, p):
        fname = p["field"]
        if fname not in ent.regs:
            self._reject(ev, "unknown field for conflict resolution")
            return
        if not self._valid_field_value(ent.etype, fname, p["value"]):
            self._reject(ev, "resolution value is not valid for this field")
            return
        self._write(ent, fname, ev, p["value"])
        if ent.etype == "shelter" and fname == "capacity":
            self._check_capacity(ent, ev)

    def _on_conflict_acknowledged(self, ent, ev, p):
        inv = ent.extra.get("invariant") if ent.etype == "shelter" else None
        if inv is None or inv["conflict_id"] != p["conflict_id"]:
            return  # acknowledging something no longer open is harmless
        inv.update(status="acknowledged", rationale=p["note"], resolved_by=ev.event_id)

    @staticmethod
    def _valid_field_value(etype: str, fname: str, value) -> bool:
        if (etype, fname) in EXTRA_FIELD_VALUES:
            return EXTRA_FIELD_VALUES[(etype, fname)](value)
        model = FIELD_MODELS.get(etype)
        if model is None or value is None:
            return False
        try:
            model.model_validate({fname: value})
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ people
    def _on_person_status_updated(self, ent, ev, p):
        self._write(ent, "status", ev, p["status"])
        if p.get("evidence"):
            ent.extra["evidence"].setdefault(ev.event_id, {"note_id": ev.event_id, "ciphertext": p["evidence"],
                                                           "origin": ev.origin, "at": ev.created_at})

    def _on_person_verified(self, ent, ev, p):
        ent.extra["verified_refs"].add(p["status_event_id"])
        ent.updated_at = max(ent.updated_at, ev.created_at)

    def _on_person_evidence_added(self, ent, ev, p):
        ent.extra["evidence"].setdefault(p["note_id"], {"note_id": p["note_id"], "ciphertext": p["ciphertext"],
                                                        "origin": ev.origin, "at": ev.created_at})
        ent.updated_at = max(ent.updated_at, ev.created_at)

    # ------------------------------------------------------------------ supplies: escrow rights
    @staticmethod
    def _credit(ent: Entity, node: str, ev: Ev, amount: int) -> None:
        ent.extra["credits"].setdefault(node, []).append((ev, amount))

    @staticmethod
    def rights_known(ent: Entity, node: str, ev: Ev) -> int:
        """Rights `node` could have known it held when it created `ev` (causal past only)."""
        known = sum(a for c, a in ent.extra["credits"].get(node, [])
                    if c.event_id == ev.event_id or happened_before(c, ev))
        return known - ent.extra["debits"].get(node, 0)

    def _debit(self, ent: Entity, ev: Ev, amount: int) -> bool:
        have = self.rights_known(ent, ev.origin, ev)
        if have < amount:
            self._reject(ev, f"insufficient allocation rights: {ev.origin} held {have}, needed {amount}")
            return False
        ent.extra["debits"][ev.origin] = ent.extra["debits"].get(ev.origin, 0) + amount
        return True

    def _on_supply_restocked(self, ent, ev, p):
        if ent.extra["kind"] != "quantity":
            self._reject(ev, "cannot restock an indivisible resource")
            return
        ent.extra["total"] += p["amount"]
        self._credit(ent, ev.origin, ev, p["amount"])

    def _on_supply_rights_transferred(self, ent, ev, p):
        x = ent.extra
        if x["kind"] != "quantity":
            self._reject(ev, "use token transfer for indivisible resources")
            return
        if p["to_node"] == ev.origin:
            self._reject(ev, "cannot transfer rights to self")
            return
        if not self._debit(ent, ev, p["amount"]):
            return
        self._credit(ent, p["to_node"], ev, p["amount"])
        x["transfers"].append({"transfer_id": p["transfer_id"], "from_node": ev.origin, "to_node": p["to_node"],
                               "amount": p["amount"], "epoch": None, "request_id": p.get("request_id"),
                               "event_id": ev.event_id, "at": ev.created_at})
        self._fulfil_request(ent, p.get("request_id"))

    def _on_supply_token_transferred(self, ent, ev, p):
        x = ent.extra
        if x["kind"] != "indivisible":
            self._reject(ev, "token transfer only applies to indivisible resources")
            return
        if x["holder"] != ev.origin or p["epoch"] != x["epoch"]:
            self._reject(ev, f"stale or unauthorised token transfer: holder is {x['holder']} at epoch {x['epoch']}")
            return
        if any(a["status"] == "reserved" for a in x["allocations"].values()):
            self._reject(ev, "cannot transfer authority while the resource is allocated")
            return
        x["transfers"].append({"transfer_id": p["transfer_id"], "from_node": ev.origin, "to_node": p["to_node"],
                               "amount": 1, "epoch": x["epoch"], "request_id": p.get("request_id"),
                               "event_id": ev.event_id, "at": ev.created_at})
        x["holder"], x["epoch"] = p["to_node"], x["epoch"] + 1
        self._fulfil_request(ent, p.get("request_id"))

    def _on_supply_allocated(self, ent, ev, p):
        x = ent.extra
        if p["allocation_id"] in x["allocations"]:
            self._reject(ev, "duplicate allocation id")
            return
        if x["kind"] == "indivisible":
            if p["amount"] != 1:
                self._reject(ev, "indivisible resources are allocated as a single unit")
                return
            if x["holder"] != ev.origin or p.get("epoch") != x["epoch"]:
                self._reject(ev, f"{ev.origin} does not hold the allocation token "
                                 f"(holder {x['holder']}, epoch {x['epoch']})")
                return
            if not x["in_service"] or any(a["status"] == "reserved" for a in x["allocations"].values()):
                self._reject(ev, "resource is not available")
                return
        elif not self._debit(ent, ev, p["amount"]):
            return
        x["reserved"] += p["amount"]
        x["allocations"][p["allocation_id"]] = {
            "allocation_id": p["allocation_id"], "node_id": ev.origin, "amount": p["amount"],
            "target_type": p["target_type"], "target_id": p.get("target_id"), "status": "reserved",
            "epoch": p.get("epoch"), "note": p.get("note", ""), "event_id": ev.event_id,
            "created_at": ev.created_at, "updated_at": ev.created_at}

    def _own_active_allocation(self, ent, ev, p):
        a = ent.extra["allocations"].get(p["allocation_id"])
        if a is None or a["status"] != "reserved":
            self._reject(ev, "allocation is not active")
            return None
        if a["node_id"] != ev.origin:
            self._reject(ev, "only the allocating node may change this allocation")
            return None
        return a

    def _on_supply_allocation_released(self, ent, ev, p):
        a = self._own_active_allocation(ent, ev, p)
        if a is None:
            return
        a.update(status="released", updated_at=ev.created_at)
        ent.extra["reserved"] -= a["amount"]
        if ent.extra["kind"] == "quantity":
            self._credit(ent, ev.origin, ev, a["amount"])

    def _on_supply_distributed(self, ent, ev, p):
        if ent.extra["kind"] != "quantity":
            self._reject(ev, "indivisible resources are released, not distributed")
            return
        a = self._own_active_allocation(ent, ev, p)
        if a is None:
            return
        a.update(status="distributed", updated_at=ev.created_at)
        ent.extra["reserved"] -= a["amount"]
        ent.extra["distributed"] += a["amount"]

    def _on_supply_written_off(self, ent, ev, p):
        if ent.extra["kind"] != "quantity":
            self._reject(ev, "use availability changes for indivisible resources")
            return
        if self._debit(ent, ev, p["amount"]):
            ent.extra["unavailable"] += p["amount"]

    def _on_supply_availability_changed(self, ent, ev, p):
        x = ent.extra
        if x["kind"] != "indivisible" or x["holder"] != ev.origin:
            self._reject(ev, "only the token holder can change availability")
            return
        if any(a["status"] == "reserved" for a in x["allocations"].values()):
            self._reject(ev, "release the allocation first")
            return
        x["in_service"] = p["available"]

    def _on_supply_rights_requested(self, ent, ev, p):
        ent.extra["requests"].setdefault(p["request_id"], {
            "request_id": p["request_id"], "requested_by": ev.origin, "amount": p["amount"],
            "reason": p["reason"], "status": "pending", "at": ev.created_at})

    def _on_supply_request_declined(self, ent, ev, p):
        r = ent.extra["requests"].get(p["request_id"])
        if r and r["status"] == "pending":
            r["status"] = "declined"

    @staticmethod
    def _fulfil_request(ent, request_id):
        r = ent.extra["requests"].get(request_id) if request_id else None
        if r and r["status"] == "pending":
            r["status"] = "fulfilled"

    # ------------------------------------------------------------------ finish
    def _finish(self) -> None:
        for person in self.people.values():
            reg = person.regs["status"]
            win = reg.winner_event()
            if reg.disputed():
                person.extra["verification"] = "disputed"
            elif win is not None and win.event_id in person.extra["verified_refs"]:
                person.extra["verification"] = "verified"
            else:
                person.extra["verification"] = "unverified"
            person.extra["status_event_id"] = win.event_id if win else ""

    def supply_balances(self, ent: Entity) -> dict[str, int]:
        x = ent.extra
        if x["kind"] == "indivisible":
            return {x["holder"]: 1} if x["holder"] else {}
        nodes = set(x["credits"]) | set(x["debits"])
        return {n: sum(a for _, a in x["credits"].get(n, [])) - x["debits"].get(n, 0) for n in sorted(nodes)}

    # ------------------------------------------------------------------ persistence
    def write(self, conn) -> None:
        """Replace all materialized tables inside the caller's transaction."""
        from .db import PROJECTION_TABLES
        for t in PROJECTION_TABLES:
            conn.execute(f"DELETE FROM {t}")
        ins = lambda table, row: conn.execute(  # noqa: E731
            f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        dumps = lambda v: json.dumps(v, sort_keys=True)  # noqa: E731

        for e in self.teams.values():
            ins("teams", {"team_id": e.eid, "name": e.get("name"), "specialty": e.get("specialty"),
                          "members": e.get("members"), "home_node": e.extra["home_node"],
                          "created_at": e.created_at, "updated_at": e.updated_at})
        for e in self.needs.values():
            ins("needs", {"need_id": e.eid, **{k: e.get(k) for k in NEED_POLICY}, "origin": e.origin,
                          "created_at": e.created_at, "updated_at": e.updated_at,
                          "disputed_fields": dumps(e.disputed_fields())})
            for n in e.extra["notes"].values():
                ins("need_notes", {"need_id": e.eid, **n})
        for e in self.shelters.values():
            cap, occ = e.get("capacity"), e.extra["occupancy"]
            ins("shelters", {"shelter_id": e.eid, **{k: e.get(k) for k in SHELTER_POLICY}, "occupancy": occ,
                             "available_space": max(cap - occ, 0), "origin": e.origin,
                             "created_at": e.created_at, "updated_at": e.updated_at,
                             "disputed_fields": dumps(e.disputed_fields())})
            for u in e.extra["updates"]:
                ins("shelter_updates", {"shelter_id": e.eid, **u})
        for e in self.tasks.values():
            status_reg = e.regs["status"]
            win = status_reg.winner_event()
            ins("tasks", {"task_id": e.eid, **{k: e.get(k) for k in TASK_POLICY}, "need_id": e.extra["need_id"],
                          "origin": e.origin, "created_at": e.created_at, "updated_at": e.updated_at,
                          "completed_at": win.created_at if e.get("status") == "completed" and win else None,
                          "disputed_fields": dumps(e.disputed_fields())})
            for n in e.extra["notes"].values():
                ins("task_notes", {"task_id": e.eid, **n})
        for e in self.people.values():
            ins("people", {"person_id": e.eid, "status": e.get("status"),
                           "status_event_id": e.extra["status_event_id"], "verification": e.extra["verification"],
                           "reporting_team": e.get("reporting_team"), "sensitive": dumps(e.extra["sensitive"]),
                           "origin": e.origin, "created_at": e.created_at, "updated_at": e.updated_at})
            for n in e.extra["evidence"].values():
                ins("people_evidence", {"person_id": e.eid, **n})
        for e in self.supplies.values():
            x = e.extra
            balances = self.supply_balances(e)
            if x["kind"] == "indivisible":
                allocated = any(a["status"] == "reserved" for a in x["allocations"].values())
                status = "allocated" if allocated else ("available" if x["in_service"] else "unavailable")
                available = 1 if status == "available" else 0
                unavailable = 1 if status == "unavailable" else 0
            else:
                available, unavailable = sum(balances.values()), x["unavailable"]
                status = "available" if available > 0 else "exhausted"
            ins("supplies", {"supply_id": e.eid, **{k: e.get(k) for k in SUPPLY_POLICY}, "kind": x["kind"],
                             "unit": x["unit"], "total": x["total"], "available": available,
                             "reserved": x["reserved"], "distributed": x["distributed"], "unavailable": unavailable,
                             "holder": x["holder"], "epoch": x["epoch"], "status": status, "origin": e.origin,
                             "created_at": e.created_at, "updated_at": e.updated_at})
            for node, bal in balances.items():
                ins("allocation_rights", {"supply_id": e.eid, "node_id": node, "balance": bal})
            for t in x["transfers"]:
                ins("rights_transfers", {"supply_id": e.eid, **t})
            for a in x["allocations"].values():
                ins("resource_allocations", {"supply_id": e.eid, **a})
            for r in x["requests"].values():
                ins("rights_requests", {"supply_id": e.eid, **r})
        for c in self.conflicts.values():
            ins("conflicts", {**c, "candidates": dumps(c["candidates"]), "current_value": dumps(c["current_value"])})
        for r in self.rejected.values():
            ins("rejected_events", r)
