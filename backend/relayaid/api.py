"""HTTP API for one node.

/api/*   operator API used by the dashboard (bearer-token sessions, role checks)
/peer/*  node-to-node sync protocol (Ed25519-signed, gzip bodies, replay-protected)
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from . import events as E
from .config import NodeConfig
from .node import Node, OpError
from .security import AuthError, NonceCache, Session, sign_response, verify_peer_request
from .sync import SyncEngine, SyncError, decode, encode, metrics

log = logging.getLogger("relayaid.api")


# ------------------------------------------------------------------------------ request bodies
class Login(BaseModel):
    username: str = Field(max_length=64)
    passphrase: str = Field(max_length=256)


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class FieldsBody(BaseModel):
    fields: dict[str, Any]


class OccupancyBody(BaseModel):
    delta: int
    reason: str = Field(default="", max_length=200)


class PersonBody(BaseModel):
    status: E.PersonStatus = "missing"
    reporting_team: str = Field(default="", max_length=64)
    name: Optional[str] = Field(default=None, max_length=200)
    age: Optional[str] = Field(default=None, max_length=20)
    description: Optional[str] = Field(default=None, max_length=1000)
    last_known_location: Optional[str] = Field(default=None, max_length=200)
    contact: Optional[str] = Field(default=None, max_length=200)


class PersonStatusBody(BaseModel):
    status: E.PersonStatus
    evidence: Optional[str] = Field(default=None, max_length=1000)


class AssignBody(BaseModel):
    team_id: Optional[str] = Field(default=None, max_length=64)


class TaskStatusBody(BaseModel):
    status: E.TaskStatus
    note: str = Field(default="", max_length=300)


class AllocateBody(BaseModel):
    amount: int = Field(default=1, ge=1, le=1_000_000)
    target_type: E.TargetType = "none"
    target_id: Optional[str] = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=200)


class TransferBody(BaseModel):
    to_node: str = Field(max_length=64)
    amount: int = Field(default=1, ge=1, le=1_000_000)
    request_id: Optional[str] = Field(default=None, max_length=64)


class AmountBody(BaseModel):
    amount: int = Field(ge=1, le=1_000_000)
    note: str = Field(default="", max_length=200)


class AvailabilityBody(BaseModel):
    available: bool
    reason: str = Field(default="", max_length=200)


class RequestBody(BaseModel):
    amount: int = Field(default=1, ge=1, le=1_000_000)
    reason: str = Field(default="", max_length=300)


class DeclineBody(BaseModel):
    reason: str = Field(default="", max_length=300)


class ResolveBody(BaseModel):
    value: Any = None
    rationale: str = Field(min_length=1, max_length=500)


class AckBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class SyncBody(BaseModel):
    peer_id: str = Field(max_length=64)
    trigger: Literal["manual", "lab"] = "manual"


class SimBody(BaseModel):
    offline: Optional[bool] = None
    blocked_peers: Optional[list[str]] = None
    latency_ms: Optional[int] = Field(default=None, ge=0, le=10_000)
    drop_rate: Optional[float] = Field(default=None, ge=0, le=0.95)
    batch_size: Optional[int] = Field(default=None, ge=1, le=1000)
    window_batches: Optional[int] = Field(default=None, ge=0, le=1000)
    duplicate_delivery: Optional[bool] = None
    shuffle: Optional[bool] = None
    auto_sync: Optional[bool] = None
    auto_sync_interval_s: Optional[int] = Field(default=None, ge=1, le=300)


def create_app(cfg: NodeConfig, transport=None, background_sync: bool = True) -> FastAPI:
    node = Node(cfg)
    engine = SyncEngine(node, transport)
    nonces = NonceCache()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if background_sync:
            engine.start_background()
        yield
        engine.stop_background()

    app = FastAPI(title=f"RelayAid node {cfg.node_id}", version=__version__, lifespan=lifespan)
    app.state.node, app.state.engine = node, engine
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(OpError)
    async def _op_error(_, exc: OpError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(AuthError)
    async def _auth_error(_, exc: AuthError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    # -------------------------------------------------------------- auth dependencies
    def session(authorization: Optional[str] = Header(default=None)) -> Session:
        token = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else None
        return node.sessions.get(token)

    def writer(s: Session = Depends(session)) -> Session:
        if not s.perms["write"]:
            raise AuthError("this role is read-only", 403)
        return s

    def coordinator(s: Session = Depends(session)) -> Session:
        if not s.perms["lab"]:
            raise AuthError("coordinator role required", 403)
        return s

    def resolver(s: Session = Depends(session)) -> Session:
        if not s.perms["resolve"]:
            raise AuthError("only coordinators may resolve conflicts", 403)
        return s

    # -------------------------------------------------------------- public / auth
    @app.get("/api/health")
    def health():
        sim = node.sim.get()
        return {"node_id": cfg.node_id, "team": cfg.team, "role": cfg.role, "version": __version__,
                "offline": sim["offline"], "requires_login": bool(cfg.operators)}

    @app.post("/api/auth/login")
    def login(body: Login):
        if not cfg.operators:
            raise AuthError("this node has no operator accounts", 403)
        token, s = node.sessions.login(body.username, body.passphrase)
        node.audit(body.username, "login", f"role={s.role}")
        return {"token": token, **s.public()}

    @app.post("/api/auth/logout")
    def logout(authorization: Optional[str] = Header(default=None)):
        if authorization and " " in authorization:
            node.sessions.logout(authorization.split(" ", 1)[1])
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(s: Session = Depends(session)):
        return s.public()

    # Read-only server status works without an operator account so the dashboard can show the hub.
    def session_or_server(authorization: Optional[str] = Header(default=None)) -> Optional[Session]:
        if cfg.is_server and not cfg.operators:
            return None
        return session(authorization)

    # -------------------------------------------------------------- node status
    @app.get("/api/node")
    def node_status(_=Depends(session_or_server)):
        sim = node.sim.get()
        peers = []
        rows = {r["peer_id"]: r for r in node.db.query("SELECT * FROM peer_state")}
        for p in cfg.peers():
            st = rows.get(p.node_id, {})
            peer_vv = json.loads(st.get("peer_vv") or "{}")
            peers.append({"node_id": p.node_id, "team": p.team, "role": p.role, "url": p.url,
                          "link_up": node.sim.link_up(p.node_id),
                          "last_success_at": st.get("last_success_at"), "last_attempt_at": st.get("last_attempt_at"),
                          "consecutive_failures": st.get("consecutive_failures", 0), "last_error": st.get("last_error"),
                          "pending_events": node.pending_for(peer_vv), "peer_vv": peer_vv})
        last = node.db.one("SELECT MAX(started_at) t FROM sync_sessions WHERE status IN ('success','partial')")
        return {"node_id": cfg.node_id, "team": cfg.team, "role": cfg.role, "url": cfg.url,
                "deployment_id": cfg.deployment_id, "version_vector": node.version_vector(),
                "lamport": node.lamport, "event_count": node.event_count(), "sim": sim, "peers": peers,
                "last_sync_at": last["t"] if last else None, "db_path": cfg.db_path.name,
                "db_ok": node.db.one("PRAGMA quick_check")["quick_check"] == "ok",
                "digest": node.state_digest(), "overview": node.overview()}

    @app.get("/api/state/digest")
    def digest(_=Depends(session_or_server)):
        return node.state_digest()

    @app.get("/api/metrics")
    def get_metrics(_=Depends(session_or_server)):
        return metrics(node)

    @app.get("/api/events")
    def recent_events(limit: int = 50, _=Depends(session_or_server)):
        return node.recent_events(max(1, min(limit, 500)))

    @app.get("/api/audit")
    def audit(limit: int = 100, _=Depends(coordinator)):
        return node.db.query("SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (max(1, min(limit, 500)),))

    # -------------------------------------------------------------- teams / needs
    @app.get("/api/teams")
    def teams(_=Depends(session_or_server)):
        return node.list_teams()

    @app.post("/api/teams")
    def create_team(body: E.TeamFields, s=Depends(writer)):
        if not body.name:
            raise OpError("name is required")
        return node.register_team(body.model_dump(exclude_none=True), s.username)

    @app.get("/api/needs")
    def needs(_=Depends(session_or_server)):
        return node.list_needs()

    @app.post("/api/needs")
    def create_need(body: E.NeedCreated, s=Depends(writer)):
        return node.create_need(body.model_dump(mode="json"), s.username)

    @app.patch("/api/needs/{nid}")
    def update_need(nid: str, body: E.NeedFields, s=Depends(writer)):
        return node.update_need(nid, body.model_dump(mode="json", exclude_none=True), s.username)

    @app.post("/api/needs/{nid}/notes")
    def need_note(nid: str, body: NoteBody, s=Depends(writer)):
        return node.add_note("need", nid, body.text, s.username)

    @app.get("/api/history/{entity_type}/{eid}")
    def history(entity_type: Literal["need", "shelter", "task", "supply", "team", "person"], eid: str,
                s=Depends(session_or_server)):
        if entity_type == "person" and (s is None or not s.perms["write"]):
            raise AuthError("not permitted", 403)
        return node.history(entity_type, eid)

    # -------------------------------------------------------------- shelters
    @app.get("/api/shelters")
    def shelters(_=Depends(session_or_server)):
        return node.list_shelters()

    @app.post("/api/shelters")
    def create_shelter(body: E.ShelterRegistered, s=Depends(writer)):
        return node.register_shelter(body.model_dump(mode="json"), s.username)

    @app.patch("/api/shelters/{sid}")
    def update_shelter(sid: str, body: E.ShelterFields, s=Depends(writer)):
        return node.update_shelter(sid, body.model_dump(mode="json", exclude_none=True), s.username)

    @app.post("/api/shelters/{sid}/occupancy")
    def occupancy(sid: str, body: OccupancyBody, s=Depends(writer)):
        return node.change_occupancy(sid, body.delta, body.reason, s.username)

    @app.post("/api/shelters/{sid}/notes")
    def shelter_note(sid: str, body: NoteBody, s=Depends(writer)):
        return node.add_note("shelter", sid, body.text, s.username)

    # -------------------------------------------------------------- tasks
    @app.get("/api/tasks")
    def tasks(_=Depends(session_or_server)):
        return node.list_tasks()

    @app.post("/api/tasks")
    def create_task(body: E.TaskCreated, s=Depends(writer)):
        return node.create_task(body.model_dump(mode="json"), s.username)

    @app.patch("/api/tasks/{tid}")
    def update_task(tid: str, body: E.TaskFields, s=Depends(writer)):
        return node.update_task(tid, body.model_dump(mode="json", exclude_none=True), s.username)

    @app.post("/api/tasks/{tid}/assign")
    def assign(tid: str, body: AssignBody, s=Depends(writer)):
        return node.assign_task(tid, body.team_id, s.username)

    @app.post("/api/tasks/{tid}/status")
    def task_status(tid: str, body: TaskStatusBody, s=Depends(writer)):
        return node.set_task_status(tid, body.status, body.note, s.username)

    @app.post("/api/tasks/{tid}/notes")
    def task_note(tid: str, body: NoteBody, s=Depends(writer)):
        return node.add_note("task", tid, body.text, s.username)

    # -------------------------------------------------------------- people (protected)
    @app.get("/api/people")
    def people(s=Depends(session)):
        if not s.perms["write"]:
            raise AuthError("viewers cannot access person records", 403)
        detailed = s.perms["people_details"]
        if detailed:
            node.audit(s.username, "people_records_viewed", "decrypted list")
        return node.list_people(s.dek, detailed)

    @app.post("/api/people")
    def report_person(body: PersonBody, s=Depends(writer)):
        return node.report_person(body.model_dump(), s.dek, s.username)

    @app.post("/api/people/{pid}/status")
    def person_status(pid: str, body: PersonStatusBody, s=Depends(writer)):
        return node.update_person_status(pid, body.status, body.evidence, s.dek, s.username)

    @app.post("/api/people/{pid}/verify")
    def verify_person(pid: str, s=Depends(resolver)):
        return node.verify_person(pid, s.username)

    @app.post("/api/people/{pid}/evidence")
    def person_evidence(pid: str, body: NoteBody, s=Depends(writer)):
        return node.add_person_evidence(pid, body.text, s.dek, s.username)

    # -------------------------------------------------------------- supplies & allocation
    @app.get("/api/supplies")
    def supplies(_=Depends(session_or_server)):
        return node.list_supplies()

    @app.post("/api/supplies")
    def register_supply(body: E.SupplyRegistered, supply_id: Optional[str] = None, s=Depends(writer)):
        data = body.model_dump(mode="json")
        if supply_id:
            data["supply_id"] = supply_id
        return node.register_supply(data, s.username)

    @app.patch("/api/supplies/{sid}")
    def update_supply(sid: str, body: E.SupplyFields, s=Depends(writer)):
        return node.update_supply(sid, body.model_dump(mode="json", exclude_none=True), s.username)

    @app.post("/api/supplies/{sid}/allocate")
    def allocate(sid: str, body: AllocateBody, s=Depends(writer)):
        return node.allocate(sid, body.amount, body.target_type, body.target_id, body.note, s.username)

    @app.post("/api/supplies/{sid}/transfer")
    def transfer(sid: str, body: TransferBody, s=Depends(writer)):
        return node.transfer_rights(sid, body.to_node, body.amount, body.request_id, s.username)

    @app.post("/api/supplies/{sid}/restock")
    def restock(sid: str, body: AmountBody, s=Depends(writer)):
        return node.restock(sid, body.amount, body.note, s.username)

    @app.post("/api/supplies/{sid}/write-off")
    def write_off(sid: str, body: AmountBody, s=Depends(writer)):
        return node.write_off(sid, body.amount, body.note, s.username)

    @app.post("/api/supplies/{sid}/availability")
    def availability(sid: str, body: AvailabilityBody, s=Depends(writer)):
        return node.set_availability(sid, body.available, body.reason, s.username)

    @app.post("/api/supplies/{sid}/request-rights")
    def request_rights(sid: str, body: RequestBody, s=Depends(writer)):
        return node.request_rights(sid, body.amount, body.reason, s.username)

    @app.post("/api/supplies/{sid}/requests/{rid}/decline")
    def decline(sid: str, rid: str, body: DeclineBody, s=Depends(writer)):
        return node.decline_request(sid, rid, body.reason, s.username)

    @app.post("/api/allocations/{aid}/release")
    def release(aid: str, s=Depends(writer)):
        return node.release_allocation(aid, s.username)

    @app.post("/api/allocations/{aid}/distribute")
    def distribute(aid: str, s=Depends(writer)):
        return node.distribute_allocation(aid, s.username)

    @app.get("/api/allocations/verify")
    def verify_allocations(_=Depends(session_or_server)):
        return node.verify_allocations()

    # -------------------------------------------------------------- conflicts
    @app.get("/api/conflicts")
    def conflicts(status: Optional[str] = None, _=Depends(session_or_server)):
        return node.list_conflicts(status)

    @app.post("/api/conflicts/{cid}/resolve")
    def resolve(cid: str, body: ResolveBody, s=Depends(resolver)):
        return node.resolve_conflict(cid, body.value, body.rationale, s.username)

    @app.post("/api/conflicts/{cid}/acknowledge")
    def acknowledge(cid: str, body: AckBody, s=Depends(resolver)):
        return node.acknowledge_conflict(cid, body.note, s.username)

    # -------------------------------------------------------------- sync control
    @app.post("/api/sync/run")
    def run_sync(body: SyncBody, s=Depends(writer)):
        try:
            return engine.sync_with(body.peer_id, trigger=body.trigger)
        except SyncError as exc:
            raise OpError(str(exc))

    @app.get("/api/sync/sessions")
    def sync_sessions(limit: int = 50, _=Depends(session_or_server)):
        return node.db.query("SELECT * FROM sync_sessions ORDER BY session_id DESC LIMIT ?", (max(1, min(limit, 500)),))

    # -------------------------------------------------------------- network lab
    @app.get("/api/sim")
    def get_sim(_=Depends(session_or_server)):
        return node.sim.get()

    @app.put("/api/sim")
    def put_sim(body: SimBody, s=Depends(coordinator)):
        changes = body.model_dump(exclude_none=True)
        if "blocked_peers" in changes:
            unknown = set(changes["blocked_peers"]) - set(cfg.roster)
            if unknown:
                raise OpError(f"unknown peers: {sorted(unknown)}")
        state = node.sim.update(**changes)
        node.audit(s.username, "network_sim_changed", json.dumps(changes)[:300])
        return state

    @app.post("/api/sim/reset")
    def reset_sim(s=Depends(coordinator)):
        node.audit(s.username, "network_sim_reset")
        return node.sim.reset()

    @app.post("/api/lock")
    def lock_node(s=Depends(coordinator)):
        n = node.sessions.lock_all()
        node.audit(s.username, "node_locked", f"{n} session(s) ended, keys discarded")
        return {"locked": True, "sessions_ended": n}

    # -------------------------------------------------------------- peer protocol
    async def peer_endpoint(request: Request, path: str) -> Response:
        body = await request.body()
        peer_id = verify_peer_request(cfg, nonces, "POST", path, request.headers, body)
        if not node.sim.link_up(peer_id):  # receiver side of the simulated partition
            return JSONResponse({"detail": "link_down"}, status_code=503)
        try:
            msg, _ = decode(body)
            result = engine.handle(path, peer_id, msg)
        except (ValueError, OSError, SyncError) as exc:
            return JSONResponse({"detail": f"bad request: {type(exc).__name__}"}, status_code=400)
        out, _ = encode(result)
        headers = sign_response(node.signer, cfg.node_id, request.headers["x-relayaid-nonce"], out)
        return Response(out, media_type="application/json", headers={**headers, "Content-Encoding": "identity",
                                                                       "X-RelayAid-Body": "gzip"})

    @app.post("/peer/handshake")
    async def peer_handshake(request: Request):
        return await peer_endpoint(request, "/peer/handshake")

    @app.post("/peer/pull")
    async def peer_pull(request: Request):
        return await peer_endpoint(request, "/peer/pull")

    @app.post("/peer/push")
    async def peer_push(request: Request):
        return await peer_endpoint(request, "/peer/push")

    return app
