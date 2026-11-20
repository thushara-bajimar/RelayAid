# RelayAid - Presentation Content (12 slides)

All numbers are measured results from `TESTING_AND_EVALUATION.md`. Screenshots are in `docs/screenshots/`.

---

## Slide 1 - RelayAid: Coordinate Without Connection
- Offline-first disaster coordination and resilient data synchronization
- Domain: Data Engineering & Distributed Systems
- Visual: dashboard overview (`01-overview.png`)

**Speaker notes:** When towers fall and the internet is shut down, relief teams still have to coordinate. RelayAid lets them keep working on their own devices and merges everything safely when devices meet.

## Slide 2 - The Problem: Coordination During Network Failure
- Floods, earthquakes, conflicts, shutdowns remove connectivity
- Teams keep acting on partial information
- Updates conflict, get overwritten, or are lost
- Two teams can dispatch the same boat
- Lost or seized devices expose data about vulnerable people

**Speaker notes:** The failure modes are not only "no internet" - they are silent data loss, unsafe allocation and privacy exposure.

## Slide 3 - Why Centralized Approaches Fail
| Approach | Breaks because |
|---|---|
| Central server apps | No connection, no work |
| Offline mode + last-write-wins | Silently overwrites concurrent updates |
| Consensus / locks | Need a reachable majority |
| Plain CRDT merging | Agrees after the fact, cannot un-dispatch a boat |

## Slide 4 - Proposed Offline-First Solution
- Every device is a full replica: own database, own key, own clocks
- Every change is an immutable signed event
- Devices exchange only missing events, peer-to-peer or via an optional server
- State = deterministic projection of events
- Escrow rights and exclusive tokens for scarce resources
- Person data encrypted before it is stored

## Slide 5 - Distributed Architecture
- Alpha, Bravo, Charlie + optional server: four separate processes, four SQLite files
- Same code everywhere; server holds no data key and creates no records
- Visual: architecture diagram (nodes in a triangle, dashed links to server) and `13-nodes.png`

## Slide 6 - Event-Based Synchronization
- Event = id, origin, sequence, Lamport clock, version vector, payload, signature
- Handshake → pull missing (causal order, bounded batches) → push → acknowledgement
- Idempotent (duplicates ignored), resumable, gzip, signed
- Measured: ~129 bytes/event in bulk; one-event sync ~660 B; no-op sync ~320 B regardless of history
- Visual: sequence diagram of handshake / pull / push

## Slide 7 - Conflict Resolution and Convergence
- Per-field semantics: LWW, max-lattice, multi-value (critical), counter, grow-only set
- "Found" vs "missing" → both kept, marked disputed, coordinator resolves with rationale
- Conflicts derived from events → identical on every node; cannot vanish by late sync
- Convergence checked by SHA-256 state digests
- Measured: 40/40 partitioned trials and 25/25 chaos runs converged, no lost updates
- Visual: `07-conflict-center.png`

## Slide 8 - Double-Allocation Prevention
- CRDTs guarantee agreement, not safety
- Escrow rights: spend only rights in your causal past
- Exclusive token + epoch for boats, ambulances, generators
- Re-validated on every replica (forged events rejected)
- Measured: 216 unsafe attempts refused, 0 double allocations, 10/10 forged events rejected
- Trade-off: no rights → wait or request; everything else keeps working
- Visual: `15-allocation-blocked.png`, `03-supplies-allocation.png`

## Slide 9 - Network Partition Demonstration
- Partition Lab: cut any link, offline nodes, latency, loss, short windows, duplicates, reordering
- Four guided scenarios from the brief
- Measured: 394 ms to converge four real processes after a partition heals; 15/15 converged even at 50 % packet loss
- Visual: `08-partition-lab.png`, `09-lab-scenario3-double-allocation.png`

## Slide 10 - Security and Privacy
- Ed25519 device identities; signed requests, responses and events
- Replay protection (time window + nonces); strict schemas
- AES-256-GCM per sensitive field; key unlocked only by operator passphrase
- Server never holds the key; roles: coordinator / responder / viewer
- Honest limits: loopback HTTP in MVP, coordination data not encrypted at rest
- Visual: `16-people-responder-redacted.png`

## Slide 11 - Measured Results and Limitations
| Metric | Result |
|---|---|
| Automated tests | 65 / 65 passing |
| Convergence | 40/40 + 25/25 runs, 0 lost updates |
| Allocation safety | 0 double allocations; stock never exceeded |
| Loss tolerance | converged at 10/30/50 % loss |
| Rebuild cost | 9.6 / 110 / 312 ms at 100 / 1k / 3k events |
- Limitations: linear rebuild, no compaction, laptop-scale transport, keys not hardware-protected
- Visual: E2 bar chart (mean convergence ms by sync order)

## Slide 12 - Conclusion and Future Scope
- Coordination continues through network failure without silent loss
- Critical disagreements stay visible; scarce resources stay safe
- Future: mobile + Bluetooth/Wi-Fi Direct/LoRa, QR pairing, TLS, SQLCipher, snapshots, rights leases, token recovery, maps
- Closing line: *"When the network dies, coordination doesn't have to."*
