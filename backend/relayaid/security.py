"""Operator authentication, sessions and peer request authentication.

Operators
    Each field node has local operator accounts (coordinator / responder / viewer) with
    scrypt-hashed passphrases. Login returns a random bearer token that expires after
    `session_minutes`. For roles that may handle person records, login also *unwraps the
    data-encryption key* with the passphrase; the key lives only in memory, inside the
    session, and is discarded on logout or expiry. Repeated failures lock the account briefly.

Peers
    Every peer-to-peer request is signed by the sender's Ed25519 device key over
    method, path, timestamp, nonce and the SHA-256 of the body. The receiver checks roster
    membership, a +/-120 s timestamp window and a nonce cache (replay protection).
    Responses are signed too, binding them to the request nonce, so authentication is mutual.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

from . import crypto
from .config import OPERATOR_ROLES, NodeConfig

CLOCK_SKEW_S = 120
MAX_FAILURES = 5
LOCKOUT_S = 60


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass
class Session:
    username: str
    role: str
    perms: dict
    expires_at: float
    dek: bytes | None

    def public(self) -> dict:
        return {"username": self.username, "role": self.role, "permissions": self.perms,
                "expires_at": self.expires_at, "can_decrypt": self.dek is not None}


class SessionStore:
    def __init__(self, cfg: NodeConfig):
        self.cfg = cfg
        self._sessions: dict[str, Session] = {}
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def login(self, username: str, passphrase: str) -> tuple[str, Session]:
        with self._lock:
            count, until = self._failures.get(username, (0, 0.0))
            if until > time.time():
                raise AuthError("too many failed attempts; try again shortly", 429)
        op = self.cfg.operators.get(username)
        if op is None or not crypto.check_passphrase(passphrase, op["passphrase_hash"]):
            with self._lock:
                count += 1
                self._failures[username] = (count, time.time() + LOCKOUT_S if count >= MAX_FAILURES else 0.0)
            raise AuthError("invalid credentials")
        dek = crypto.unwrap_key(passphrase, op["wrapped_dek"]) if op.get("wrapped_dek") else None
        token = secrets.token_urlsafe(32)
        session = Session(username, op["role"], OPERATOR_ROLES[op["role"]],
                          time.time() + self.cfg.session_minutes * 60, dek)
        with self._lock:
            self._failures.pop(username, None)
            self._sessions[self._key(token)] = session
        return token, session

    def get(self, token: str | None) -> Session:
        if not token:
            raise AuthError("authentication required")
        with self._lock:
            s = self._sessions.get(self._key(token))
            if s is None:
                raise AuthError("invalid or expired session")
            if s.expires_at < time.time():
                self._sessions.pop(self._key(token), None)
                s.dek = None
                raise AuthError("session expired; node locked")
            return s

    def logout(self, token: str) -> None:
        with self._lock:
            s = self._sessions.pop(self._key(token), None)
            if s:
                s.dek = None

    def lock_all(self) -> int:
        with self._lock:
            n = len(self._sessions)
            for s in self._sessions.values():
                s.dek = None
            self._sessions.clear()
            return n


# ------------------------------------------------------------------------------ peers
def _string_to_sign(method: str, path: str, ts: str, nonce: str, body: bytes) -> bytes:
    return f"{method.upper()}\n{path}\n{ts}\n{nonce}\n{crypto.sha256_hex(body)}".encode()


def sign_request(signer: crypto.Signer, node_id: str, method: str, path: str, body: bytes) -> dict[str, str]:
    ts, nonce = str(int(time.time() * 1000)), secrets.token_hex(16)
    return {"X-RelayAid-Node": node_id, "X-RelayAid-Timestamp": ts, "X-RelayAid-Nonce": nonce,
            "X-RelayAid-Signature": signer.sign(_string_to_sign(method, path, ts, nonce, body)),
            "Content-Encoding": "gzip", "Content-Type": "application/json"}


def sign_response(signer: crypto.Signer, node_id: str, request_nonce: str, body: bytes) -> dict[str, str]:
    return {"X-RelayAid-Node": node_id,
            "X-RelayAid-Signature": signer.sign(f"response\n{request_nonce}\n{crypto.sha256_hex(body)}".encode())}


def verify_response(cfg: NodeConfig, expected_peer: str, request_nonce: str, headers, body: bytes) -> None:
    peer = cfg.roster.get(expected_peer)
    sig = headers.get("x-relayaid-signature") or headers.get("X-RelayAid-Signature")
    sender = headers.get("x-relayaid-node") or headers.get("X-RelayAid-Node")
    if peer is None or sender != expected_peer or not sig or not crypto.verify_signature(
            peer.public_key, f"response\n{request_nonce}\n{crypto.sha256_hex(body)}".encode(), sig):
        raise AuthError("peer response signature invalid")


class NonceCache:
    def __init__(self):
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_add(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            if len(self._seen) > 50_000:
                self._seen = {k: t for k, t in self._seen.items() if now - t < CLOCK_SKEW_S * 2}
            if key in self._seen:
                return False
            self._seen[key] = now
            return True


def verify_peer_request(cfg: NodeConfig, nonces: NonceCache, method: str, path: str, headers, body: bytes) -> str:
    """Return the authenticated peer id or raise AuthError."""
    g = lambda k: headers.get(k) or headers.get(k.lower())  # noqa: E731
    node_id, ts, nonce, sig = (g("X-RelayAid-Node"), g("X-RelayAid-Timestamp"),
                               g("X-RelayAid-Nonce"), g("X-RelayAid-Signature"))
    if not all((node_id, ts, nonce, sig)):
        raise AuthError("missing peer authentication headers")
    peer = cfg.roster.get(node_id)
    if peer is None or node_id == cfg.node_id:
        raise AuthError("unknown peer", 403)
    try:
        skew = abs(time.time() - int(ts) / 1000)
    except ValueError:
        raise AuthError("bad timestamp")
    if skew > CLOCK_SKEW_S:
        raise AuthError("request timestamp outside allowed window")
    if not crypto.verify_signature(peer.public_key, _string_to_sign(method, path, ts, nonce, body), sig):
        raise AuthError("invalid peer signature", 403)
    if not nonces.check_and_add(f"{node_id}:{nonce}"):
        raise AuthError("replayed request rejected", 409)
    return node_id
