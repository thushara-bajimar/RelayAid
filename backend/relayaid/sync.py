"""Event-based anti-entropy synchronization protocol.

A session is initiated by node A towards peer B (another node or the central server):

    1. HANDSHAKE  A -> B  {vv_A}             B -> A {vv_B, role}
    2. PULL       A -> B  {vv_A, cursor, limit}
                  B -> A  {events B has that vv_A does not cover, next cursor, has_more}
                  repeated until has_more = false (or the sync window closes)
    3. PUSH       A -> B  {events A has that vv_B does not cover}          (bounded batches)
                  B -> A  ACK {accepted ids, duplicates, rejected, new vv_B}

Properties
    * incremental: only events above the other side's version vector are sent
    * causal order: batches are ordered by (lamport, origin, seq), so a prefix of the stream
      is causally closed and per-origin contiguous - a session cut short still leaves a
      consistent replica, and the next session resumes from the new version vector
    * idempotent: receivers deduplicate by event_id, so retries and duplicate deliveries are safe
    * authenticated: every request and response is signed (see security.py)
    * compact: bodies are gzip-compressed canonical JSON; wire and raw sizes are measured
"""
from __future__ import annotations

import gzip
import json
import logging
import random
import threading
import time
from typing import Protocol

import httpx

from . import crypto
from .netsim import LinkDown, SimulatedDrop
from .node import Node
from .security import AuthError, sign_request, verify_response

log = logging.getLogger("relayaid.sync")
MAX_RETRIES = 3


class SyncError(Exception):
    pass


class Transport(Protocol):
    def post(self, base_url: str, path: str, body: bytes, headers: dict) -> tuple[int, dict, bytes]: ...


class HttpTransport:
    def __init__(self, timeout: float = 10.0):
        self.client = httpx.Client(timeout=timeout)

    def post(self, base_url: str, path: str, body: bytes, headers: dict) -> tuple[int, dict, bytes]:
        r = self.client.post(base_url.rstrip("/") + path, content=body, headers=headers)
        return r.status_code, dict(r.headers), r.content


def encode(obj) -> tuple[bytes, int]:
    raw = crypto.canonical(obj)
    return gzip.compress(raw, compresslevel=6), len(raw)


def decode(body: bytes) -> tuple[dict, int]:
    if len(body) > 8 * 1024 * 1024:
        raise SyncError("message too large")
    raw = gzip.decompress(body)
    return json.loads(raw), len(raw)


class SyncEngine:
    def __init__(self, node: Node, transport: Transport | None = None, backoff_base: float = 0.2):
        self.node = node
        self.transport = transport or HttpTransport()
        self.backoff_base = backoff_base
        self._peer_locks: dict[str, threading.Lock] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ transport helper
    def _call(self, peer_id: str, path: str, obj: dict, stats: dict) -> dict:
        node, peer = self.node, self.node.cfg.roster[peer_id]
        body, raw_len = encode(obj)
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            node.sim.check_link(peer_id)  # a cut link produces no traffic at all
            headers = sign_request(node.signer, node.node_id, "POST", path, body)
            try:
                node.sim.before_request()
                status, rh, rbody = self.transport.post(peer.url, path, body, headers)
            except (SimulatedDrop, httpx.TransportError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                stats["retries"] += 1 if attempt < MAX_RETRIES else 0
                if attempt < MAX_RETRIES:
                    time.sleep(self.backoff_base * (2 ** attempt))
                continue
            stats["bytes_sent"] += len(body)
            stats["raw_bytes_sent"] += raw_len
            if status == 503:
                raise LinkDown(f"{peer_id} reports the link is down")
            if status != 200:
                raise SyncError(f"{peer_id} answered HTTP {status}: {rbody[:200]!r}")
            verify_response(node.cfg, peer_id, headers["X-RelayAid-Nonce"], {k.lower(): v for k, v in rh.items()},
                            rbody)
            data, rlen = decode(rbody)
            stats["bytes_received"] += len(rbody)
            stats["raw_bytes_received"] += rlen
            return data
        raise SyncError(f"request to {peer_id}{path} failed after {MAX_RETRIES} retries ({last_error})")

    # ------------------------------------------------------------------ session
    def sync_with(self, peer_id: str, trigger: str = "manual") -> dict:
        node = self.node
        if peer_id not in node.cfg.roster or peer_id == node.node_id:
            raise SyncError(f"unknown peer {peer_id}")
        lock = self._peer_locks.setdefault(peer_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return {"peer_id": peer_id, "status": "busy"}
        try:
            return self._session(peer_id, trigger)
        finally:
            lock.release()

    def _session(self, peer_id: str, trigger: str) -> dict:
        node = self.node
        sim = node.sim.get()
        stats = dict(events_pulled=0, events_pushed=0, duplicates=0, rejected=0, batches=0, retries=0,
                     bytes_sent=0, bytes_received=0, raw_bytes_sent=0, raw_bytes_received=0)
        started, t0 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), time.perf_counter()
        status, error, window_hit, peer_vv = "success", None, False, None
        limit, window = sim["batch_size"], sim["window_batches"]
        try:
            hello = self._call(peer_id, "/peer/handshake", {"node_id": node.node_id, "vv": node.version_vector(),
                                                            "deployment_id": node.cfg.deployment_id}, stats)
            if hello.get("deployment_id") != node.cfg.deployment_id:
                raise AuthError("peer belongs to a different deployment")
            peer_vv = hello["vv"]

            # ---- pull: what the peer has and we lack
            my_vv, cursor, more = node.version_vector(), None, True
            while more:
                if window and stats["batches"] >= window:
                    window_hit = True
                    break
                resp = self._call(peer_id, "/peer/pull", {"vv": my_vv, "cursor": cursor, "limit": limit}, stats)
                stats["batches"] += 1
                batch = resp["events"]
                deliveries = [batch, batch] if sim["duplicate_delivery"] else [batch]
                for delivery in deliveries:
                    delivery = list(delivery)
                    if sim["shuffle"]:
                        random.shuffle(delivery)
                    ack = node.ingest(delivery, received_from=peer_id)
                    stats["events_pulled"] += len(ack["accepted"])
                    stats["duplicates"] += ack["duplicates"]
                    stats["rejected"] += len(ack["rejected"])
                cursor, more = resp["cursor"], resp["has_more"]

            # ---- push: what we have and the peer lacks (bounded batches, acknowledged)
            push_cursor = None
            while not window_hit:
                if window and stats["batches"] >= window:
                    window_hit = True
                    break
                batch, push_cursor, more = node.missing_for(peer_vv, push_cursor, limit)
                if not batch:
                    break
                sends = 2 if sim["duplicate_delivery"] else 1
                for _ in range(sends):
                    ack = self._call(peer_id, "/peer/push", {"events": batch}, stats)
                    stats["events_pushed"] += len(ack["accepted"])
                    stats["duplicates"] += ack["duplicates"]
                    stats["rejected"] += len(ack["rejected"])
                stats["batches"] += 1
                peer_vv = ack["vv"]
                if not more:
                    break
            if window_hit:
                status = "partial"
        except LinkDown as exc:
            status, error = "link_down", str(exc)
        except (SyncError, AuthError, KeyError, ValueError, OSError) as exc:
            status = "partial" if stats["events_pulled"] or stats["events_pushed"] else "failed"
            error = f"{type(exc).__name__}: {exc}"
        duration = (time.perf_counter() - t0) * 1000
        self._record(peer_id, trigger, started, duration, status, stats, error, peer_vv)
        if status not in ("link_down",):
            log.info("sync %s<->%s %s pulled=%d pushed=%d dup=%d %.1fms", node.node_id, peer_id, status,
                     stats["events_pulled"], stats["events_pushed"], stats["duplicates"], duration)
        return {"peer_id": peer_id, "status": status, "error": error, "duration_ms": round(duration, 2), **stats}

    def _record(self, peer_id, trigger, started, duration, status, stats, error, peer_vv) -> None:
        db = self.node.db
        db.execute(
            "INSERT INTO sync_sessions (peer_id, initiator, trigger, started_at, duration_ms, status, events_pulled,"
            " events_pushed, duplicates, rejected, batches, retries, bytes_sent, bytes_received, raw_bytes_sent,"
            " raw_bytes_received, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (peer_id, self.node.node_id, trigger, started, duration, status, stats["events_pulled"],
             stats["events_pushed"], stats["duplicates"], stats["rejected"], stats["batches"], stats["retries"],
             stats["bytes_sent"], stats["bytes_received"], stats["raw_bytes_sent"], stats["raw_bytes_received"],
             error))
        db.execute("INSERT OR IGNORE INTO peer_state (peer_id) VALUES (?)", (peer_id,))
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        if status in ("success", "partial"):
            db.execute("UPDATE peer_state SET last_attempt_at=?, last_success_at=?, consecutive_failures=0,"
                       " next_attempt_at=0, last_error=NULL, peer_vv=? WHERE peer_id=?",
                       (now, now, json.dumps(peer_vv or {}), peer_id))
        else:
            row = db.one("SELECT consecutive_failures FROM peer_state WHERE peer_id=?", (peer_id,))
            fails = (row["consecutive_failures"] if row else 0) + 1
            backoff = min(60, 2 ** min(fails, 6))  # exponential backoff for the auto-sync loop
            db.execute("UPDATE peer_state SET last_attempt_at=?, consecutive_failures=?, next_attempt_at=?,"
                       " last_error=? WHERE peer_id=?", (now, fails, time.time() + backoff, error, peer_id))
            if peer_vv:
                db.execute("UPDATE peer_state SET peer_vv=? WHERE peer_id=?", (json.dumps(peer_vv), peer_id))

    # ------------------------------------------------------------------ server side
    def handle(self, path: str, peer_id: str, msg: dict) -> dict:
        node = self.node
        if path == "/peer/handshake":
            node.db.execute("INSERT OR IGNORE INTO peer_state (peer_id) VALUES (?)", (peer_id,))
            if isinstance(msg.get("vv"), dict):
                node.db.execute("UPDATE peer_state SET peer_vv=? WHERE peer_id=?", (json.dumps(msg["vv"]), peer_id))
            return {"node_id": node.node_id, "role": node.cfg.role, "vv": node.version_vector(),
                    "deployment_id": node.cfg.deployment_id}
        if path == "/peer/pull":
            vv, cursor = msg.get("vv") or {}, msg.get("cursor")
            limit = max(1, min(int(msg.get("limit", 100)), 1000))
            if not isinstance(vv, dict) or (cursor is not None and (not isinstance(cursor, list) or len(cursor) != 3)):
                raise ValueError("malformed pull request")
            events, cursor, more = node.missing_for(vv, cursor, limit)
            node.db.bump("events_served", len(events))
            return {"events": events, "cursor": cursor, "has_more": more}
        if path == "/peer/push":
            evs = msg.get("events")
            if not isinstance(evs, list) or len(evs) > 1000:
                raise ValueError("malformed push request")
            ack = node.ingest(evs, received_from=peer_id)
            node.db.execute("UPDATE peer_state SET peer_vv=? WHERE peer_id=?", (json.dumps(ack["vv"]), peer_id))
            return ack
        raise ValueError("unknown peer endpoint")

    # ------------------------------------------------------------------ background gossip
    def start_background(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name=f"sync-{self.node.node_id}", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            sim = self.node.sim.get()
            if sim["auto_sync"] and not sim["offline"]:
                for peer in self.node.cfg.peers():
                    if self._stop.is_set():
                        break
                    if not self.node.sim.link_up(peer.node_id):
                        continue
                    row = self.node.db.one("SELECT next_attempt_at FROM peer_state WHERE peer_id=?", (peer.node_id,))
                    if row and row["next_attempt_at"] > time.time():
                        continue
                    try:
                        self.sync_with(peer.node_id, trigger="auto")
                    except Exception:  # keep the loop alive; failures are recorded per session
                        log.exception("auto sync failed")
            self._stop.wait(max(1, int(sim.get("auto_sync_interval_s", 5))))


def metrics(node: Node) -> dict:
    db = node.db
    agg = db.one(
        "SELECT COUNT(*) sessions, SUM(status='success') ok, SUM(status='partial') partial,"
        " SUM(status='failed') failed, SUM(status='link_down') link_down,"
        " COALESCE(SUM(events_pulled),0) pulled, COALESCE(SUM(events_pushed),0) pushed,"
        " COALESCE(SUM(duplicates),0) duplicates, COALESCE(SUM(retries),0) retries,"
        " COALESCE(SUM(bytes_sent),0) bytes_sent, COALESCE(SUM(bytes_received),0) bytes_received,"
        " COALESCE(SUM(raw_bytes_sent),0) raw_sent, COALESCE(SUM(raw_bytes_received),0) raw_received,"
        " AVG(CASE WHEN status IN ('success','partial') THEN duration_ms END) avg_ms,"
        " MAX(duration_ms) max_ms FROM sync_sessions")
    attempted = (agg["sessions"] or 0) - (agg["link_down"] or 0)
    transferred = agg["pulled"] + agg["pushed"]
    conflicts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM conflicts GROUP BY status")}
    return {
        "node_id": node.node_id,
        "sessions": agg["sessions"] or 0,
        "sessions_success": agg["ok"] or 0,
        "sessions_partial": agg["partial"] or 0,
        "sessions_failed": agg["failed"] or 0,
        "sessions_link_down": agg["link_down"] or 0,
        "success_rate": round(((agg["ok"] or 0) + (agg["partial"] or 0)) / attempted, 4) if attempted else None,
        "events_transferred": transferred,
        "events_pulled": agg["pulled"], "events_pushed": agg["pushed"],
        "duplicates_in_sessions": agg["duplicates"],
        "retries": agg["retries"],
        "bytes_sent": agg["bytes_sent"], "bytes_received": agg["bytes_received"],
        "raw_bytes_sent": agg["raw_sent"], "raw_bytes_received": agg["raw_received"],
        "avg_bytes_per_event": round((agg["bytes_sent"] + agg["bytes_received"]) / transferred, 1) if transferred else None,
        "compression_ratio": round((agg["raw_sent"] + agg["raw_received"]) / max(1, agg["bytes_sent"] + agg["bytes_received"]), 2)
        if transferred else None,
        "avg_session_ms": round(agg["avg_ms"], 2) if agg["avg_ms"] else None,
        "max_session_ms": round(agg["max_ms"], 2) if agg["max_ms"] else None,
        "counters": db.counters(),
        "conflicts": conflicts,
        "event_count": node.event_count(),
        "rejected_by_validation": len(node.projection.rejected),
        "awaiting_dependencies": len(node.projection.awaiting_deps),
        "last_rebuild_ms": round(node.last_rebuild_ms, 2),
    }
