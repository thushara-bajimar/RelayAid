# Testing and Evaluation

All results in this document were **measured** on the reference machine (Windows 11, Python 3.14.6, Intel Core laptop CPU) on 2026-11-20. Raw data: [`docs/results/benchmark_results.json`](docs/results/benchmark_results.json); regenerate with `python scripts/benchmark.py`.

We distinguish four kinds of statement:

- **Demonstrated** - observed in automated tests or measured runs listed here.
- **Guaranteed by design** - follows from the algorithm under stated assumptions (argued in DISTRIBUTED_SYSTEMS_DESIGN.md), and exercised but not exhaustively proven by tests.
- **Assumptions** - conditions the guarantees depend on.
- **Limitations** - known gaps.

## 1. Test methodology

### Harness
`backend/tests/conftest.py` builds a complete deployment per test: hub + Alpha + Bravo + Charlie, each with its **own SQLite file, identity key and FastAPI app**. Peer HTTP requests are routed by a `RouterTransport` into the target node's app through FastAPI's TestClient, so the real protocol runs end to end: request and response signatures, gzip bodies, replay protection, version-vector diffing, link simulation on both ends. Helpers: `partition(...)`, `heal()`, `sync(a, b)`, `full_mesh_sync()`, `digests()`.

### Assertion style
- **Convergence** is asserted by comparing SHA-256 digests of every materialized table across nodes, never by a flag.
- **No lost updates** is asserted by comparing the sets of event ids on all nodes, and by checking domain totals (e.g. occupancy equals the sum of all deltas created anywhere).
- **Conflicts** are asserted by their presence, status, candidate values and identical conflict ids across nodes.
- **Safety** is asserted with the replica-wide audit `verify_allocations()` plus explicit counts of active allocations.

### Running
```bash
cd backend
../.venv/Scripts/python -m pytest -q
```
Result on the reference machine: **65 passed** (about 80 s).

## 2. Test inventory

| Area | File | # | Scenarios |
|---|---|---|---|
| Offline operation & sync | `test_offline_and_sync.py` | 12 | Fully offline node (no packets sent, server empty); restart persistence; P2P sync without server; relay through server between nodes that never meet; incremental sync (30 → 0 → 1 events, bytes shrink); bounded batches & pagination; duplicate delivery idempotency; failed sync with retries and backoff then recovery; partial sync in a 1-batch window resuming to a consistent prefix; receiver-side partition; shuffled delivery; metrics |
| Conflict resolution | `test_conflicts.py` | 13 | Concurrent LWW edit deterministic and recorded; notes from 3 partitioned nodes all kept; priority escalates; **found vs missing** both kept, record disputed, plain update refused; open conflict survives 4 rounds of repeated sync with identical id on all nodes; explicit resolution by coordinator only, rationale and candidates preserved, converged; resolution racing a new concurrent write keeps the conflict open; concurrent occupancy deltas both counted, overflow surfaced, acknowledged, restored; task assignment conflict with lattice status; completed vs cancelled conflict; merge result independent of 3 sync orders |
| Convergence | `test_convergence.py` | 11 | Brief scenario 1; brief scenario 4 (repeated partitions); **6 randomized runs** with random partitions, random sync orders, duplicated/shuffled delivery, random batch sizes and concurrent needs/notes/edits/occupancy/allocations; replaying the full log one event at a time in 3 shuffled orders into a fresh node; conflict sets converge with all 3 competing values; vector-clock semantics |
| Allocation safety | `test_allocation_safety.py` | 12 | Brief scenario 3; token transfer moves authority exactly once (sender loses it immediately, receiver gains it only after receipt, stale third node cannot claim it); duplicate transfer messages; **forged** allocation without token rejected by every replica; stale token transfer after handover rejected; release and rights-request workflow; quantity limited by local rights under partition (and a node with no rights refused); sender cannot reuse transferred rights; duplicate transfer/allocation events; **forged** overspend rejected; rights not yet received cannot be spent (causal check); distribution/write-off/restock accounting balances |
| Security | `test_security.py` | 17 | Unauthenticated, unknown-node, mis-signed, replayed and stale peer requests; tampered event; malformed and malicious envelopes; API validation; plaintext refused in sensitive fields; role-based access (viewer/responder/coordinator) and no leakage via feed or overview; no plaintext in any SQLite file incl. WAL, server opaque and without login; no sensitive data in logs or audit; brute-force lockout; session expiry drops the key; lock endpoint; ciphertext bound to record; impostor response rejected |

The randomized convergence test also asserts that **no event created through the local API was ever rejected by the projection** - the local safety checks exactly mirror replica-wide validation.

## 3. Required demonstration scenarios

| Scenario | Automated test | Dashboard |
|---|---|---|
| 1 Independent offline updates | `test_scenario_1_independent_offline_updates` | Partition Lab → "1 · Independent offline updates" |
| 2 Conflicting edits | `test_resolution_creates_event_and_converges`, `test_merge_order_does_not_change_state`, `test_person_found_is_not_overwritten_by_concurrent_missing` | "2 · Conflicting edits" |
| 3 Double allocation attempt | `test_double_allocation_attempt_during_partition_is_blocked` | "3 · Double allocation attempt" |
| 4 Repeated partitions | `test_scenario_4_repeated_partitions` | "4 · Repeated partitions" |

Observed dashboard run of scenario 1 (lab log): digests while partitioned `6ce61887 / 03369ae6 / 8bba244e` → after ring sync `43ef9ca6` on all three nodes. Scenario 3 log: "Bravo allocation BLOCKED: bravo does not hold the exclusive allocation authority …"; after sync each node reports exactly 1 active allocation, by Alpha.

## 4. Experiments (benchmark.py)

E1-E7 run in-process nodes (separate SQLite files, full signed/gzip protocol over an ASGI transport, no sockets). E8 runs four separate OS processes communicating over loopback HTTP.

### E1 - Synchronization payload and incrementality
Alpha creates N needs; Bravo syncs; then Alpha adds one need and Bravo syncs again; then a sync with nothing new.

| Events | Initial sync | Wire bytes | Raw JSON bytes | Wire bytes / event | Compression | Batches | +1 event sync | No-op sync |
|---|---|---|---|---|---|---|---|---|
| 10 | 43 ms | 1,770 | 5,221 | 177 | 2.95× | 1 | 658 B | 321 B, 24 ms |
| 100 | 145 ms | 12,916 | 50,488 | 129 | 3.91× | 1 | 657 B | 323 B, 21 ms |
| 1,000 | 1,647 ms | 129,114 | 507,559 | 129 | 3.93× | 10 | 667 B | 324 B, 31 ms |

**Findings.** Cost of synchronizing an already-synchronized pair is constant (~320 B) and a one-event update costs ~660 B regardless of history size: synchronization is incremental, not a database copy. Bulk transfer costs ~129 B per event after compression (3.9× smaller than raw JSON). Batches are bounded at 100 events.

### E2 - Convergence after a three-way partition
Each trial: shared shelter and need; full partition; every node performs *k* random operations (new needs, occupancy deltas, notes, concurrent edits of the shared need's location and status); heal; sync rounds in a given order until digests are equal and a full round moves nothing. 5 trials per row (different seeds).

| Order | Updates / node | Converged | No lost updates | Mean time | Max time | Rounds | Events moved | Wire | Open conflicts (mean) |
|---|---|---|---|---|---|---|---|---|---|
| ring | 20 | 5/5 | 5/5 | 317 ms | 324 ms | 2 | 120 | 19.2 KB | 0.6 |
| ring | 100 | 5/5 | 5/5 | 748 ms | 897 ms | 2 | 600 | 78.9 KB | 1.0 |
| reverse ring | 20 | 5/5 | 5/5 | 323 ms | 327 ms | 2 | 120 | 19.2 KB | 0.6 |
| reverse ring | 100 | 5/5 | 5/5 | 789 ms | 850 ms | 2 | 600 | 79.0 KB | 1.0 |
| star via server | 20 | 5/5 | 5/5 | 446 ms | 461 ms | 3 | 182 | 28.0 KB | 0.6 |
| star via server | 100 | 5/5 | 5/5 | 1,209 ms | 1,296 ms | 3 | 902 | 115.3 KB | 1.0 |
| full mesh | 20 | 5/5 | 5/5 | 473 ms | 508 ms | 2 | 120 | 20.9 KB | 0.6 |
| full mesh | 100 | 5/5 | 5/5 | 981 ms | 1,035 ms | 2 | 600 | 79.8 KB | 1.0 |

Times include the final verification round. **40/40 trials converged with no lost updates.** The ring needs the minimum event movement (each event crosses each other node once); the star moves more because every event also goes through the server. Open conflicts are expected: concurrent status edits of the shared need are a critical field and correctly remain open.

### E3 - Randomized partition chaos
25 seeds. Per node: random duplicate delivery, shuffled batches, batch size 2/5/50, sync window 1/3/unlimited batches. Six phases of random partitions (4 shapes), 4-10 random writes each (needs, occupancy, notes, concurrent capacity edits), 1-4 random syncs per phase. Then heal and full-mesh sync.

| Metric | Result |
|---|---|
| Runs converged (equal digests) | **25 / 25** |
| Runs with identical event sets on all nodes (no lost updates) | **25 / 25** |
| Runs with identical conflict sets on all nodes | **25 / 25** |
| Writes performed | 1,043 |
| Duplicate deliveries ignored | 1,021 |
| Runs ending with an open conflict | 25 (concurrent capacity edits are critical; expected) |

### E4 - Allocation safety under partitions
One boat (token at Alpha) and 300 medical kits (rights Alpha 100 / Bravo 100 / Charlie 100). 300 random steps: every 15 steps a random partition and 3 random syncs; random nodes attempt boat allocations, kit allocations of 1-15, token transfers and releases. Afterwards 10 **forged, validly signed** allocation events (spending the boat at a wrong epoch or 500 kits) are injected from Charlie, then everything is healed and synced.

| Metric | Result |
|---|---|
| Allocation attempts | 267 |
| Accepted (node held rights/token) | 51 |
| Refused locally (409) | 216 (plus 16 refused token transfers; counter total 232) |
| Successful token transfers | 0 (the boat was allocated most of the time, so transfers were correctly refused) |
| Boat active allocations at the end | **1** |
| Kits reserved / total | **300 / 300 - never exceeded** |
| Forged events rejected by every replica | **10 / 10** |
| Replica-wide audit (`verify_allocations`) | **OK on all 3 nodes** |

### E5 - Duplicate delivery
200 events, every batch delivered twice on every node, ring sync until converged: **converged**, **400 duplicates ignored**, 200 needs on each node (no duplicates in state).

### E6 - Lossy links
Each node 30 local needs; per-request loss probability *p*; batch size 10; full-mesh rounds until converged (max 12).

| Loss *p* | Trials converged | Sessions | Session success rate | Retries |
|---|---|---|---|---|
| 0.1 | 5/5 | 60 | 100 % | 23 |
| 0.3 | 5/5 | 60 | 95.0 % | 70 |
| 0.5 | 5/5 | 66 | 80.3 % | 157 |

Retries with exponential backoff absorb most losses; sessions that still fail keep their completed batches and the next round finishes the work. **15/15 converged even at 50 % loss.**

### E7 - Projection rebuild cost
| Events in log | Median full rebuild | One local write incl. rebuild |
|---|---|---|
| 100 | 9.6 ms | 20.4 ms |
| 1,000 | 109.7 ms | 113.7 ms |
| 3,000 | 311.8 ms | 859.2 ms (single sample) |

Rebuild time grows linearly (~0.1 ms per event). This is the main scalability limit of the MVP design; snapshots/incremental folding are future work.

### E8 - Real processes over HTTP
Four OS processes (`relayaid.serve`), auto-sync off, full partition, 50 needs created on each field node through the HTTP API, heal, ring sync via `/api/sync/run`.

| Metric | Result |
|---|---|
| Mean local write latency (HTTP round trip incl. rebuild) | 8.36 ms |
| Converged | **yes**, in 2 rounds |
| Convergence time after healing | **394 ms** |
| Wire traffic | 40.6 KB |

## 5. Required metrics summary

| # | Metric | Measured value | Source |
|---|---|---|---|
| 1 | Convergence success rate | 40/40 (E2), 25/25 (E3), 15/15 (E6), E5 and E8 converged | E2, E3, E5, E6, E8 |
| 2 | Convergence time | 317-1,209 ms mean in-process (E2); 394 ms between real processes (E8) | E2, E8 |
| 3 | Updates preserved | All event sets identical in every run | E2, E3 |
| 4 | Lost-update tests | 0 lost in 65 runs; occupancy equals sum of all deltas | E2, E3, tests |
| 5 | Conflict detection | Every concurrent critical edit produced an open conflict with all candidates, identical on all nodes (25/25 runs) | E3, tests |
| 6 | Conflict resolution results | Explicit resolutions converge with rationale; races keep conflicts open | tests |
| 7 | Double allocation attempts blocked | 216 local refusals; 0 double allocations | E4 |
| 8 | Allocation safety | Audit OK; boat ≤ 1 active allocation; kits ≤ stock; 10/10 forged events rejected | E4 |
| 9 | Payload size | ~129 B/event bulk; ~660 B for a one-event sync; ~320 B no-op | E1 |
| 10 | Sync duration | 21-31 ms no-op; 1.6 s for 1,000 events | E1 |
| 11 | Events transferred | 120 / 600 per 3-node convergence (ring) | E2 |
| 12 | Duplicates handled | 400 (E5), 1,021 (E3) | E3, E5 |
| 13 | Sync failure rate | Sessions not fully successful: 0 % at 10 % loss, 5 % at 30 %, 19.7 % at 50 % (all trials still converged) | E6 |
| 14 | Offline operation | All operations succeed with every link cut; no packets sent | tests |
| 15 | Data protection | 17/17 security tests pass; no plaintext in any database file | tests |

## 6. Theoretical guarantees vs demonstrated results

| Property | Guaranteed by design (assumptions) | Demonstrated |
|---|---|---|
| Convergence | Same event set ⇒ same state (deterministic fold; same code version) | Digest equality in all tests and 80+ benchmark runs |
| Eventual delivery | Every event reaches every node (eventual connectivity) | All partitions in tests/benchmarks healed and delivered |
| No silent loss of critical updates | Concurrent critical writes stay as candidates until a dominating write | Tests on capacity, status, assignment, person status |
| No double allocation | Escrow and token invariants hold in every execution (honest local checks; validation for others) | E4 and allocation tests, incl. forged events |
| Authenticity / integrity | Ed25519 unforgeable without private key | Tamper, forge, replay tests |
| Confidentiality of person data | AES-GCM; key never on server; passphrase strength | Byte scan of all DB files |

## 7. Limitations of the evaluation

- All runs on one laptop; no real radios, no real packet loss (loss is simulated per request).
- E1-E7 bypass sockets; E8 uses loopback only.
- Three field nodes; scaling to many nodes (vector size, gossip fan-out) is not measured.
- Event counts up to a few thousand; rebuild cost grows linearly (E7).
- Randomized tests sample the state space; they are not a formal proof.
- Security tests cover the implemented mechanisms; no external penetration test was performed.
