"""Event model.

Every change in RelayAid is an immutable, signed, append-only event. Materialized tables
(needs, shelters, ...) are *derived* from the event log by `projection.py`; they are never
synchronized directly.

Event envelope
--------------
event_id     random 128-bit id (stable across all replicas, used for deduplication)
origin       node id that created the event
seq          per-origin sequence number 1, 2, 3, ... (contiguous)
lamport      Lamport clock: max(local, every seen event) + 1, gives a causal total order
vv           version vector of the origin *including* this event: {node: highest seq seen}.
             It encodes the causal past exactly: a happened-before b  <=>  b.vv[a.origin] >= a.seq
entity_type / entity_id / type / payload
created_at   wall-clock time (informational only, never used for ordering)
schema       payload schema version
sig          Ed25519 signature of the origin over the canonical envelope
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import SCHEMA_VERSION, crypto

MAX_PAYLOAD_BYTES = 16_384
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

# --------------------------------------------------------------------------- enums
NeedCategory = Literal["water", "food", "medical", "shelter", "evacuation", "rescue", "sanitation", "other"]
Priority = Literal["low", "medium", "high", "critical"]
NeedStatus = Literal["open", "in_progress", "fulfilled", "cancelled"]
ShelterStatus = Literal["open", "full", "closed"]
SupplyKind = Literal["quantity", "indivisible"]
Condition = Literal["good", "fair", "damaged"]
PersonStatus = Literal["missing", "located", "found", "reunited", "needs_assistance", "unknown"]
TaskStatus = Literal["open", "assigned", "in_progress", "completed", "cancelled"]
TargetType = Literal["need", "task", "none"]

Short = Field(min_length=1, max_length=120)
Text = Field(default="", max_length=1000)


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _cipher(v: Any) -> Any:
    if v is not None and not crypto.is_ciphertext(v):
        raise ValueError("sensitive field must be encrypted before it enters an event")
    return v


# --------------------------------------------------------------------------- teams
class TeamRegistered(_P):
    name: str = Short
    specialty: str = Field(default="general", max_length=80)
    members: int = Field(default=1, ge=0, le=10_000)
    home_node: str = Field(max_length=64)


class TeamFields(_P):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    specialty: Optional[str] = Field(default=None, max_length=80)
    members: Optional[int] = Field(default=None, ge=0, le=10_000)


class TeamUpdated(_P):
    fields: TeamFields


# --------------------------------------------------------------------------- needs
class NeedCreated(_P):
    category: NeedCategory
    description: str = Field(min_length=1, max_length=500)
    quantity: int = Field(default=1, ge=0, le=1_000_000)
    unit: str = Field(default="units", max_length=24)
    priority: Priority = "medium"
    location: str = Field(default="", max_length=200)
    status: NeedStatus = "open"
    responsible_team: Optional[str] = Field(default=None, max_length=64)


class NeedFields(_P):
    category: Optional[NeedCategory] = None
    description: Optional[str] = Field(default=None, min_length=1, max_length=500)
    quantity: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    unit: Optional[str] = Field(default=None, max_length=24)
    priority: Optional[Priority] = None
    location: Optional[str] = Field(default=None, max_length=200)
    status: Optional[NeedStatus] = None
    responsible_team: Optional[str] = Field(default=None, max_length=64)


class NeedUpdated(_P):
    fields: NeedFields


class NoteAdded(_P):
    note_id: str = Field(max_length=64)
    text: str = Field(min_length=1, max_length=1000)


# --------------------------------------------------------------------------- supplies
class SupplyRegistered(_P):
    name: str = Short
    category: str = Field(default="general", max_length=60)
    kind: SupplyKind
    quantity: int = Field(default=1, ge=1, le=1_000_000)
    unit: str = Field(default="units", max_length=24)
    location: str = Field(default="", max_length=200)
    condition: Condition = "good"


class SupplyFields(_P):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=60)
    location: Optional[str] = Field(default=None, max_length=200)
    condition: Optional[Condition] = None


class SupplyUpdated(_P):
    fields: SupplyFields


class SupplyRestocked(_P):
    amount: int = Field(ge=1, le=1_000_000)
    note: str = Field(default="", max_length=200)


class RightsTransferred(_P):
    transfer_id: str = Field(max_length=64)
    to_node: str = Field(max_length=64)
    amount: int = Field(ge=1, le=1_000_000)
    request_id: Optional[str] = Field(default=None, max_length=64)


class TokenTransferred(_P):
    transfer_id: str = Field(max_length=64)
    to_node: str = Field(max_length=64)
    epoch: int = Field(ge=0)
    request_id: Optional[str] = Field(default=None, max_length=64)


class SupplyAllocated(_P):
    allocation_id: str = Field(max_length=64)
    amount: int = Field(ge=1, le=1_000_000)
    target_type: TargetType = "none"
    target_id: Optional[str] = Field(default=None, max_length=64)
    epoch: Optional[int] = Field(default=None, ge=0)
    note: str = Field(default="", max_length=200)


class AllocationRef(_P):
    allocation_id: str = Field(max_length=64)


class SupplyWrittenOff(_P):
    amount: int = Field(ge=1, le=1_000_000)
    reason: str = Field(default="", max_length=200)


class AvailabilityChanged(_P):
    available: bool
    reason: str = Field(default="", max_length=200)


class RightsRequested(_P):
    request_id: str = Field(max_length=64)
    amount: int = Field(default=1, ge=1, le=1_000_000)
    reason: str = Field(default="", max_length=300)


class RequestDeclined(_P):
    request_id: str = Field(max_length=64)
    reason: str = Field(default="", max_length=300)


# --------------------------------------------------------------------------- shelters
class ShelterRegistered(_P):
    name: str = Short
    location: str = Field(default="", max_length=200)
    capacity: int = Field(ge=0, le=1_000_000)
    accessibility: str = Field(default="", max_length=200)
    conditions: str = Field(default="", max_length=300)
    status: ShelterStatus = "open"


class ShelterFields(_P):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=200)
    capacity: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    accessibility: Optional[str] = Field(default=None, max_length=200)
    conditions: Optional[str] = Field(default=None, max_length=300)
    status: Optional[ShelterStatus] = None


class ShelterUpdated(_P):
    fields: ShelterFields


class OccupancyChanged(_P):
    delta: int = Field(ge=-100_000, le=100_000)
    reason: str = Field(default="", max_length=200)

    @field_validator("delta")
    @classmethod
    def _nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("delta must be non-zero")
        return v


# --------------------------------------------------------------------------- people (sensitive)
class SensitiveFields(_P):
    name: Optional[str] = None
    age: Optional[str] = None
    description: Optional[str] = None
    last_known_location: Optional[str] = None
    contact: Optional[str] = None

    @field_validator("*")
    @classmethod
    def _encrypted(cls, v: Any) -> Any:
        return _cipher(v)


class PersonReported(_P):
    status: PersonStatus = "missing"
    reporting_team: str = Field(default="", max_length=64)
    sensitive: SensitiveFields


class PersonStatusUpdated(_P):
    status: PersonStatus
    evidence: Optional[str] = None  # ciphertext

    @field_validator("evidence")
    @classmethod
    def _encrypted(cls, v: Any) -> Any:
        return _cipher(v)


class PersonVerified(_P):
    status_event_id: str = Field(max_length=64)


class PersonEvidenceAdded(_P):
    note_id: str = Field(max_length=64)
    ciphertext: str

    @field_validator("ciphertext")
    @classmethod
    def _encrypted(cls, v: Any) -> Any:
        return _cipher(v)


# --------------------------------------------------------------------------- tasks
class TaskCreated(_P):
    title: str = Short
    description: str = Field(default="", max_length=1000)
    priority: Priority = "medium"
    location: str = Field(default="", max_length=200)
    need_id: Optional[str] = Field(default=None, max_length=64)


class TaskFields(_P):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    priority: Optional[Priority] = None
    location: Optional[str] = Field(default=None, max_length=200)


class TaskUpdated(_P):
    fields: TaskFields


class TaskAssigned(_P):
    team_id: Optional[str] = Field(default=None, max_length=64)


class TaskStatusChanged(_P):
    status: TaskStatus
    note: str = Field(default="", max_length=300)


# --------------------------------------------------------------------------- conflicts
class ConflictResolved(_P):
    conflict_id: str = Field(max_length=64)
    field: str = Field(max_length=40)
    value: Union[str, int, None]
    rationale: str = Field(min_length=1, max_length=500)


class ConflictAcknowledged(_P):
    conflict_id: str = Field(max_length=64)
    note: str = Field(min_length=1, max_length=500)


PAYLOAD_SCHEMAS: dict[str, tuple[str, type[BaseModel]]] = {
    "team.registered": ("team", TeamRegistered),
    "team.updated": ("team", TeamUpdated),
    "need.created": ("need", NeedCreated),
    "need.updated": ("need", NeedUpdated),
    "need.note_added": ("need", NoteAdded),
    "supply.registered": ("supply", SupplyRegistered),
    "supply.updated": ("supply", SupplyUpdated),
    "supply.restocked": ("supply", SupplyRestocked),
    "supply.rights_transferred": ("supply", RightsTransferred),
    "supply.token_transferred": ("supply", TokenTransferred),
    "supply.allocated": ("supply", SupplyAllocated),
    "supply.allocation_released": ("supply", AllocationRef),
    "supply.distributed": ("supply", AllocationRef),
    "supply.written_off": ("supply", SupplyWrittenOff),
    "supply.availability_changed": ("supply", AvailabilityChanged),
    "supply.rights_requested": ("supply", RightsRequested),
    "supply.request_declined": ("supply", RequestDeclined),
    "shelter.registered": ("shelter", ShelterRegistered),
    "shelter.updated": ("shelter", ShelterUpdated),
    "shelter.occupancy_changed": ("shelter", OccupancyChanged),
    "shelter.note_added": ("shelter", NoteAdded),
    "person.reported": ("person", PersonReported),
    "person.status_updated": ("person", PersonStatusUpdated),
    "person.verified": ("person", PersonVerified),
    "person.evidence_added": ("person", PersonEvidenceAdded),
    "task.created": ("task", TaskCreated),
    "task.updated": ("task", TaskUpdated),
    "task.assigned": ("task", TaskAssigned),
    "task.status_changed": ("task", TaskStatusChanged),
    "task.note_added": ("task", NoteAdded),
    "conflict.resolved": ("*", ConflictResolved),
    "conflict.acknowledged": ("*", ConflictAcknowledged),
}

ENVELOPE_KEYS = {"event_id", "origin", "seq", "lamport", "vv", "entity_type", "entity_id", "type",
                 "payload", "created_at", "schema", "sig"}


class EventError(ValueError):
    pass


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:20]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def validate_payload(event_type: str, entity_type: str, payload: dict) -> dict:
    """Validate and normalise a payload. Raises EventError."""
    if event_type not in PAYLOAD_SCHEMAS:
        raise EventError(f"unknown event type {event_type!r}")
    expected_entity, model = PAYLOAD_SCHEMAS[event_type]
    if expected_entity != "*" and entity_type != expected_entity:
        raise EventError(f"{event_type} must target entity type {expected_entity}")
    try:
        return model.model_validate(payload).model_dump(mode="json", exclude_none=False)
    except ValidationError as exc:
        raise EventError(f"invalid payload for {event_type}: {exc.errors()[0]['msg']}") from exc


def signing_bytes(event: dict) -> bytes:
    return crypto.canonical({k: v for k, v in event.items() if k != "sig"})


def build_event(*, origin: str, seq: int, lamport: int, vv: dict[str, int], entity_type: str, entity_id: str,
                event_type: str, payload: dict, signer: crypto.Signer) -> dict:
    payload = validate_payload(event_type, entity_type, payload)
    event = {
        "event_id": new_id("ev_"),
        "origin": origin,
        "seq": seq,
        "lamport": lamport,
        "vv": dict(sorted(vv.items())),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "type": event_type,
        "payload": payload,
        "created_at": utcnow(),
        "schema": SCHEMA_VERSION,
    }
    event["sig"] = signer.sign(signing_bytes(event))
    return event


def verify_event(event: Any, roster_keys: dict[str, str]) -> dict:
    """Structural, schema, causal-metadata and signature validation of a received event.
    Returns the normalised event or raises EventError. Never trusts the sender."""
    if not isinstance(event, dict) or set(event.keys()) != ENVELOPE_KEYS:
        raise EventError("malformed envelope")
    for key in ("event_id", "origin", "entity_type", "entity_id", "type"):
        if not isinstance(event[key], str) or not ID_RE.match(event[key]):
            raise EventError(f"invalid {key}")
    if event["origin"] not in roster_keys:
        raise EventError("origin is not a member of this deployment")
    if not all(isinstance(event[k], int) and not isinstance(event[k], bool) and event[k] >= 1
               for k in ("seq", "lamport")):
        raise EventError("invalid seq/lamport")
    vv = event["vv"]
    if (not isinstance(vv, dict) or len(vv) > 64
            or not all(isinstance(k, str) and isinstance(v, int) and v >= 0 for k, v in vv.items())):
        raise EventError("invalid version vector")
    if vv.get(event["origin"]) != event["seq"]:
        raise EventError("version vector does not match seq")
    if event["lamport"] < event["seq"]:
        raise EventError("lamport clock below sequence number")
    if event["schema"] != SCHEMA_VERSION:
        raise EventError("unsupported schema version")
    if not isinstance(event["payload"], dict) or len(crypto.canonical(event["payload"])) > MAX_PAYLOAD_BYTES:
        raise EventError("payload too large or not an object")
    if not isinstance(event["sig"], str) or not crypto.verify_signature(
            roster_keys[event["origin"]], signing_bytes(event), event["sig"]):
        raise EventError("signature verification failed")
    normalised = validate_payload(event["type"], event["entity_type"], event["payload"])
    if crypto.canonical(normalised) != crypto.canonical(event["payload"]):
        raise EventError("payload is not in canonical form")
    return event
