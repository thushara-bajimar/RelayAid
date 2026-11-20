# Prompt for a presentation-generation tool

Copy everything below the line into a slide-generation tool. Attach the screenshots from `docs/screenshots/` where referenced.

---

Create a 12-slide technical presentation titled **"RelayAid - Coordinate Without Connection"** for a college engineering evaluation in Data Engineering & Distributed Systems. Audience: faculty and engineers. Tone: precise, confident, honest about limitations. Duration: 10-12 minutes.

## Design direction
- Clean, modern, light background (#F4F6F9) with a dark navy (#0F172A) title band or accent; text in near-black (#0F172A) and slate (#475569).
- Node identity colors, used consistently for diagrams: Alpha **#2A78D6** (blue), Bravo **#EB6834** (orange), Charlie **#1BAF7A** (green), Server **#64748B** (gray). Use red (#DC2626) only for "blocked/cut", green (#16A34A) only for "converged/link up".
- One sans-serif family (e.g. Inter or Segoe UI); titles 32-36 pt, body 18-20 pt; at most 5 bullets per slide, at most ~8 words per bullet.
- Prefer diagrams and screenshots over text. Every number must appear exactly as given below - do not round differently or invent any.
- No stock photos of disasters with identifiable people; abstract icons (radio tower with a cross, devices, shield, boat) are fine.

## Slides

1. **RelayAid - Coordinate Without Connection.** Subtitle: "Offline-first disaster coordination and resilient data synchronization". Use screenshot `01-overview.png` as a tilted device mock-up.
2. **The problem.** Icons for: no connectivity, conflicting updates, lost updates, same boat sent twice, lost/seized device. One line: "Coordination fails exactly when it matters most."
3. **Why centralized approaches fail.** 4-row comparison: Central apps → no connection, no work; Offline mode + last-write-wins → silent overwrites; Consensus/locks → need a majority online; Plain CRDTs → agree after the fact, cannot un-dispatch a boat.
4. **Our approach.** Five pillars as cards: Independent replicas · Signed event log · Peer-to-peer sync · Semantic merging · Escrow safety. Plus a lock icon: "Person data encrypted before storage".
5. **Distributed architecture.** Diagram: Alpha, Bravo, Charlie in a triangle with solid bidirectional arrows labeled "signed P2P sync"; Server above with dashed arrows labeled "optional"; each node shows a small database cylinder "alpha.db / bravo.db / charlie.db" and a key icon. Caption: "4 processes · 4 databases · same code · server holds no data key".
6. **Event-based synchronization.** Left: event card fields (event_id, origin, seq, Lamport clock, version vector, payload, signature). Right: 3-step sequence diagram Handshake (vectors) → Pull missing (causal order, batches of 100) → Push + ACK. Stat callouts: "~129 B per event", "~660 B one-event sync", "~320 B when nothing changed".
7. **Conflict resolution & convergence.** Table of policies: LWW → descriptions; Max-lattice → priority, task progress; Multi-value (critical) → capacity, assignment, person status; Counter → occupancy; Grow-only set → notes, evidence. Example callout: "Found vs Missing → both kept, marked disputed, coordinator decides". Screenshot `07-conflict-center.png`. Stat: "40/40 and 25/25 runs converged · 0 lost updates".
8. **Double-allocation prevention.** Left: "CRDTs guarantee agreement, not safety". Middle: escrow bar 600 water packets split Alpha 200 / Bravo 200 / Charlie 200. Right: token diagram Boat R-01 held by Alpha (epoch 0) → transfer → Bravo (epoch 1). Screenshot `15-allocation-blocked.png`. Stats: "216 unsafe attempts refused · 0 double allocations · 10/10 forged events rejected". Footer: "Trade-off: without rights, wait or request - all other work continues."
9. **Partition demonstration.** Screenshot `08-partition-lab.png` (Charlie isolated, red dashed links). Four scenario chips: Independent updates · Conflicting edits · Double allocation · Repeated partitions. Stats: "394 ms to converge 4 real processes after healing" and "Converged at 10 %, 30 %, 50 % packet loss".
10. **Security & privacy.** Two columns. Protections: Ed25519 identities & signed events; replay protection; AES-256-GCM per field; key unlocked only at login; server has no key; role-based access. Honest limits: loopback HTTP in MVP; coordination data not encrypted at rest; device keys not hardware-protected. Screenshot `16-people-responder-redacted.png`.
11. **Measured results & limitations.** Bar chart "Mean convergence time after a 3-way partition (ms)" with these exact values: ring 20: 317; ring 100: 748; reverse 20: 323; reverse 100: 789; via server 20: 446; via server 100: 1209; mesh 20: 473; mesh 100: 981. Side table: Tests 65/65 · Rebuild 9.6 / 110 / 312 ms at 100 / 1,000 / 3,000 events · Session success under loss 100 % / 95 % / 80.3 %. Limitations line: linear rebuild, no compaction, laptop-scale transport.
12. **Conclusion & future scope.** Three takeaways: keeps working offline · never silently loses critical updates · scarce resources stay safe. Future: mobile + Bluetooth/Wi-Fi Direct/LoRa, QR pairing, TLS, SQLCipher, snapshots & compaction, rights leases, token recovery, maps. Closing line: "When the network dies, coordination doesn't have to."

Add short speaker notes (2-3 sentences) to each slide explaining the point in plain language.
