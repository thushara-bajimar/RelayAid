# API Documentation

Every node exposes the same HTTP API on its own port (local defaults: hub 7400, alpha 7401, bravo 7402, charlie 7403). The dashboard reaches node `X` through the dev-server proxy at `/n/X/...`. FastAPI also serves interactive OpenAPI docs at `http://127.0.0.1:<port>/docs`.

- **Operator API** `/api/*` - JSON, bearer-token sessions, role checks.
- **Peer protocol** `/peer/*` - gzip-compressed canonical JSON, Ed25519-signed requests and responses.

## Conventions

| Item | Value |
|---|---|
| Auth header | `Authorization: Bearer <token>` from `/api/auth/login` |
| Errors | `{"detail": "..."}` (validation errors: `{"detail": [{"loc": [...], "msg": "..."}]}`) |
| 401 | missing, invalid or expired session; peer request without valid auth headers or outside the time window |
| 403 | role not permitted; unknown peer / bad peer signature; server asked to originate a field record |
| 404 | record unknown **to this node** (it may exist elsewhere and not have synced yet) |
| 409 | safety refusal: allocation without rights, disputed critical field, conflict already resolved, replayed peer request |
| 422 | payload fails validation |
| 429 | login locked after repeated failures |
| 503 | `link_down` - the simulated network link to the caller is cut |

Read endpoints on the **server** (`hub`) need no session because it has no operator accounts and no decryptable data; the server rejects all writes.

---

## Authentication

### `POST /api/auth/login`
```json
{"username": "coordinator", "passphrase": "relayaid-demo"}
```
Response:
```json
{"token": "q2V...", "username": "coordinator", "role": "coordinator",
 "permissions": {"people_details": true, "lab": true, "write": true, "resolve": true},
 "expires_at": 1795000000.0, "can_decrypt": true}
```
### `POST /api/auth/logout` · `GET /api/auth/me` · `POST /api/lock` (coordinator: end all sessions, discard keys)

---

## Node status

| Method & path | Description |
|---|---|
| `GET /api/health` | Unauthenticated liveness: node id, role, offline flag |
| `GET /api/node` | Identity, version vector, Lamport clock, event count, simulator state, per-peer link/last-sync/pending events/failures, DB integrity check, state digest, overview counts |
| `GET /api/state/digest` | `{"state": "<sha256 prefix of materialized state>", "events": "<sha256 of sorted event ids>", "event_count": n, "vv": {...}}` |
| `GET /api/metrics` | Aggregated sync statistics, counters (duplicates ignored, allocations blocked, invalid events), conflicts by status, rebuild time |
| `GET /api/events?limit=50` | Most recent events (person ciphertext shown as `[encrypted]`) |
| `GET /api/history/{entity_type}/{id}` | Full event history of one record, with rejection reasons |
| `GET /api/audit?limit=100` | Coordinator only. Local audit log (ids only) |

---

## Teams
| Method & path | Body |
|---|---|
| `GET /api/teams` | |
| `POST /api/teams` | `{"name": "North Boat Crew", "specialty": "water rescue", "members": 5}` |

## Needs
| Method & path | Body / notes |
|---|---|
| `GET /api/needs` | Includes `notes[]` and `disputed_fields[]` |
| `POST /api/needs` | `{"category": "water", "description": "Drinking water for 40 people", "quantity": 160, "unit": "litres", "priority": "critical", "location": "Ward 7", "responsible_team": "tm_..."}` |
| `PATCH /api/needs/{id}` | Any subset of the fields above plus `status` (`open/in_progress/fulfilled/cancelled`). 409 if `status` is disputed |
| `POST /api/needs/{id}/notes` | `{"text": "Tanker blocked by flood"}` |

Categories: `water food medical shelter evacuation rescue sanitation other`. Priorities: `low medium high critical`.

## Supplies and allocation
| Method & path | Body / notes |
|---|---|
| `GET /api/supplies` | Per supply: totals (`available/reserved/distributed/unavailable`), `rights` per node, `my_rights`, `holder`/`epoch` for indivisible, `allocations[]`, `transfers[]`, `requests[]` |
| `POST /api/supplies?supply_id=R-01` | `{"name": "Rescue boat R-01", "kind": "indivisible", "unit": "boat", "location": "Jetty 2"}` or `{"name": "Water packets", "kind": "quantity", "quantity": 600, "unit": "packets"}`. The registering node receives all rights / the token |
| `PATCH /api/supplies/{id}` | `name, category, location, condition` |
| `POST /api/supplies/{id}/allocate` | `{"amount": 10, "target_type": "need", "target_id": "nd_...", "note": "..."}` → `{"allocation_id", "event_id"}`; **409** when the node lacks rights or the token |
| `POST /api/supplies/{id}/transfer` | `{"to_node": "bravo", "amount": 20, "request_id": null}` - rights (quantity) or the token (indivisible) |
| `POST /api/supplies/{id}/request-rights` | `{"amount": 5, "reason": "school flooding"}` |
| `POST /api/supplies/{id}/requests/{rid}/decline` | `{"reason": "..."}` |
| `POST /api/supplies/{id}/restock` | `{"amount": 100, "note": "..."}` (quantity only; credits this node) |
| `POST /api/supplies/{id}/write-off` | `{"amount": 5, "note": "water damage"}` |
| `POST /api/supplies/{id}/availability` | `{"available": false, "reason": "engine fault"}` (indivisible, holder only) |
| `POST /api/allocations/{id}/release` | Allocating node only |
| `POST /api/allocations/{id}/distribute` | Quantity only, allocating node only |
| `GET /api/allocations/verify` | Invariant audit: `{"ok": true, "problems": [], "invalid_allocation_events_rejected": 0, ...}` |

Example refusal:
```json
HTTP 409
{"detail": "bravo does not hold the exclusive allocation authority for this resource (held by alpha, epoch 0). Request a transfer from alpha; offline allocation without the token is refused to prevent double allocation."}
```

## Shelters
| Method & path | Body |
|---|---|
| `GET /api/shelters` | Includes `occupancy`, `available_space`, recent `updates[]`, `disputed_fields[]` |
| `POST /api/shelters` | `{"name": "School", "location": "South colony", "capacity": 250, "accessibility": "ramp", "conditions": "dry", "status": "open"}` |
| `PATCH /api/shelters/{id}` | Subset of the above. 409 if `capacity` or `status` is disputed |
| `POST /api/shelters/{id}/occupancy` | `{"delta": 12, "reason": "bus from north"}` (negative for departures) → `{"occupancy", "capacity", "over_capacity"}` |
| `POST /api/shelters/{id}/notes` | `{"text": "..."}` |

## People (protected)
| Method & path | Role | Body / notes |
|---|---|---|
| `GET /api/people` | coordinator: decrypted `details` and `evidence`; responder: `details: null`; viewer: 403 | |
| `POST /api/people` | coordinator, responder | `{"status": "missing", "name": "...", "age": "...", "description": "...", "last_known_location": "...", "contact": "...", "reporting_team": "..."}` - all but status/team encrypted before storage |
| `POST /api/people/{id}/status` | coordinator, responder | `{"status": "found", "evidence": "Seen at camp B"}`; 409 while disputed |
| `POST /api/people/{id}/verify` | coordinator | Verifies the current status event; 409 while disputed |
| `POST /api/people/{id}/evidence` | coordinator, responder | `{"text": "..."}` (encrypted) |

Statuses: `missing located found reunited needs_assistance unknown`. `verification`: `unverified | verified | disputed`.

## Tasks
| Method & path | Body |
|---|---|
| `GET /api/tasks` | |
| `POST /api/tasks` | `{"title": "Deliver water", "description": "", "priority": "critical", "location": "Ward 7", "need_id": "nd_..."}` |
| `PATCH /api/tasks/{id}` | `title, description, priority, location` |
| `POST /api/tasks/{id}/assign` | `{"team_id": "tm_..."}` (null to unassign). Moves `open` → `assigned`. 409 if assignment disputed |
| `POST /api/tasks/{id}/status` | `{"status": "in_progress", "note": ""}` |
| `POST /api/tasks/{id}/notes` | `{"text": "..."}` |

## Conflicts
| Method & path | Body / notes |
|---|---|
| `GET /api/conflicts?status=open` | `conflict_id, kind (concurrent_write|invariant), entity_type, entity_id, entity_label, field, policy, status (open|resolved|auto_merged|acknowledged), candidates[{event_id, origin, lamport, created_at, value, event_type}], current_value, resolution_kind, resolved_by, rationale` |
| `POST /api/conflicts/{id}/resolve` | coordinator: `{"value": 60, "rationale": "Engineer inspection: east wing unsafe"}` → a `conflict.resolved` event |
| `POST /api/conflicts/{id}/acknowledge` | coordinator, invariant conflicts: `{"note": "15 moved to overflow tent"}` |

## Synchronization control
| Method & path | Body / notes |
|---|---|
| `POST /api/sync/run` | `{"peer_id": "bravo", "trigger": "manual"}` → session result: `status (success|partial|failed|link_down|busy), events_pulled, events_pushed, duplicates, rejected, batches, retries, bytes_sent, bytes_received, raw_bytes_sent, raw_bytes_received, duration_ms, error` |
| `GET /api/sync/sessions?limit=50` | Session log |

## Partition simulation (coordinator)
| Method & path | Body |
|---|---|
| `GET /api/sim` | Current settings |
| `PUT /api/sim` | Any of `{"offline": true, "blocked_peers": ["bravo","hub"], "latency_ms": 200, "drop_rate": 0.3, "batch_size": 20, "window_batches": 1, "duplicate_delivery": true, "shuffle": true, "auto_sync": false, "auto_sync_interval_s": 5}` |
| `POST /api/sim/reset` | Back to defaults |

"Disconnect peer" = add it to `blocked_peers` on both nodes; "server down" = add `hub` on every field node; "delayed sync" = `latency_ms`; "weak link" = `drop_rate`; "short window" = `window_batches`.

---

## Peer protocol

All peer endpoints take `POST`, a gzip-compressed canonical-JSON body, and these headers:

| Header | Meaning |
|---|---|
| `X-RelayAid-Node` | Sender node id (must be in the roster) |
| `X-RelayAid-Timestamp` | Unix ms; must be within ±120 s |
| `X-RelayAid-Nonce` | 128-bit random; single use |
| `X-RelayAid-Signature` | Ed25519 over `POST\n<path>\n<ts>\n<nonce>\n<sha256(body)>` |
| `Content-Encoding: gzip` | Body encoding |

Responses are gzip bodies signed with `X-RelayAid-Signature` over `response\n<request nonce>\n<sha256(body)>`.

| Endpoint | Request | Response |
|---|---|---|
| `/peer/handshake` | `{"node_id", "vv", "deployment_id"}` | `{"node_id", "role", "vv", "deployment_id"}` |
| `/peer/pull` | `{"vv": {...}, "cursor": [lamport, origin, seq] or null, "limit": 100}` | `{"events": [...], "cursor": [...], "has_more": bool}` |
| `/peer/push` | `{"events": [... up to 1000]}` | ACK `{"accepted": [ids], "duplicates": n, "rejected": [{"event_id", "reason"}], "vv": {...}}` |

Errors: `401/403` authentication, `409` replay, `400` malformed message, `503 {"detail":"link_down"}` simulated partition.

### Example (Python)
```python
import gzip, json, httpx
from relayaid import crypto
from relayaid.security import sign_request

body = gzip.compress(crypto.canonical({"vv": {"alpha": 3}, "cursor": None, "limit": 100}))
headers = sign_request(crypto.Signer(open("runtime-data/bravo/identity.key").read()), "bravo", "POST", "/peer/pull", body)
r = httpx.post("http://127.0.0.1:7401/peer/pull", content=body, headers=headers)
print(json.loads(gzip.decompress(r.content))["has_more"])
```

### Example (curl, operator API)
```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:7401/api/auth/login -H "Content-Type: application/json" \
  -d '{"username":"coordinator","passphrase":"relayaid-demo"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -X POST http://127.0.0.1:7401/api/needs -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"category":"water","description":"Drinking water for 40 people","quantity":160,"unit":"litres","priority":"critical"}'
curl -s -X POST http://127.0.0.1:7401/api/sync/run -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"peer_id":"bravo"}'
```
