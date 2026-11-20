# Security and Privacy

RelayAid handles information about vulnerable people in places where devices get lost, shared or seized. This document states what is protected, how, and - just as important - what is **not** protected in this MVP.

## 1. Assets

| Asset | Where it lives | Sensitivity |
|---|---|---|
| Person records (name, age, description, last-known location, contact) and evidence notes | Inside `person.*` event payloads on every node and the server | High |
| Coordination data (needs, shelters, supplies, tasks, teams) | Event payloads, materialized tables | Moderate (locations of aid can attract looting) |
| Node identity private keys | `runtime-data/<node>/identity.key` | High (impersonation) |
| Deployment data-encryption key (DEK) | Only wrapped, in `operators.json`; plaintext only in memory of a logged-in session | High |
| Operator passphrases | Only scrypt hashes on disk | High |
| Allocation rights | Derived from the event log | Integrity-critical |

## 2. Threat model

| Threat | Mitigation in RelayAid | Residual risk |
|---|---|---|
| **Lost or seized device (powered off or locked)** | Person fields are AES-256-GCM ciphertext in the database; the DEK is stored only wrapped under a scrypt key derived from the operator passphrase; sessions expire (default 60 min) and `/api/lock` discards keys immediately | Coordination data is not encrypted at rest; a weak passphrase can be brute-forced offline; the device signing key is a plain file (OS permissions only) |
| **Lost device while an operator is logged in** | Session expiry; lock endpoint | Anyone holding the unlocked device can act as that operator until expiry |
| **Unauthorized peer** | Every peer request must carry an Ed25519 signature from a roster member; unknown ids → 403, bad signatures → 403 | A stolen node key lets an attacker sync as that node until the roster is re-provisioned (no online revocation in the MVP) |
| **Replay of a captured sync request** | Signed timestamp (±120 s window) + per-sender nonce cache → 409 | Nonce cache is in memory; a restart inside the 120 s window could accept one replay (which is harmless: event ingestion is idempotent) |
| **Man-in-the-middle / impersonated responder** | Responses are signed by the expected peer and bound to the request nonce; the client rejects mismatches | Without TLS, traffic content is observable (person fields are still ciphertext) |
| **Tampered events (relay or server modifies content)** | Every event is signed by its origin; any change breaks the signature and is rejected | — |
| **Forged origin** | Signature must verify with the claimed origin's roster key | — |
| **Malicious payloads** | Strict Pydantic schema per event type (`extra="forbid"`, length and range limits), canonical-form check, 16 KB payload limit, 8 MB message limit, envelope shape and causal metadata checks | Parsing bugs in dependencies |
| **Duplicate / replayed events** | Idempotent ingestion by `event_id`; equivocation (`origin, seq` reuse) rejected | — |
| **Stale data** | Version vectors make concurrency explicit; critical fields become disputed instead of being overwritten by stale writes; stale token holders cannot allocate | Operators may act on stale data until they sync; the UI shows last-sync times and pending events |
| **Unauthorized resource allocation** | Local rights/token checks; projection re-validates every rights-spending event on every replica | A compromised node can still spend the rights it legitimately holds |
| **Compromised central server** | Server has no DEK (reads only ciphertext for people) and no operator accounts; it cannot forge events of other nodes; nodes do not depend on it | It can withhold or delay events (availability), and it can read coordination data |
| **Network partitions** | Offline-first design; see DISTRIBUTED_SYSTEMS_DESIGN.md | — |
| **Data leakage via logs / UI** | Logs carry ids and event types only; the audit log stores ids, never person data; the event feed and history redact ciphertext to `[encrypted]`; person records are absent from the overview | Browser memory and screenshots on an operator's screen |
| **Brute-force login** | scrypt hashing; 5 failures → 60 s lockout per account | Lockout counter is in memory |

## 3. Device identity and peer authentication

- **Provisioning ("pairing").** `scripts/bootstrap.py` (or `run_cluster.py` on first start) generates one Ed25519 key pair per node and a roster `{node_id, role, url, public_key}` shared by all nodes. Each private key is written only into that node's directory. In the field this corresponds to provisioning devices together before deployment; there is no discovery server.
- **Request signing.** `security.sign_request` signs `METHOD\nPATH\nTIMESTAMP\nNONCE\nSHA256(body)` and sends it in `X-RelayAid-*` headers.
- **Verification.** `security.verify_peer_request` checks roster membership, that the sender is not the receiver, the timestamp window, the signature, and the nonce cache.
- **Mutual authentication.** The responder signs `response\nNONCE\nSHA256(body)`; `verify_response` rejects anything else (`test_response_from_impostor_rejected`).
- **Event signatures** are independent of the channel: an event relayed through any number of peers or the server is still verified against its origin's key.

## 4. Operator authentication and authorization

| Role | Coordination writes | Person records | Network lab, audit log, lock | Resolve conflicts / verify people |
|---|---|---|---|---|
| coordinator | yes | create, update, **read decrypted** | yes | yes |
| responder | yes | create, update, read **redacted** (status only) | no | no |
| viewer | no (read-only) | no access | no | no |

- Passphrases are stored as scrypt hashes (N=2^14, r=8, p=1).
- Login returns a 256-bit random bearer token; the server stores only its SHA-256.
- Sessions expire after `RELAYAID_SESSION_MINUTES` (default 60). Expired sessions drop their DEK.
- The dashboard keeps tokens in `sessionStorage` (cleared when the tab closes), not `localStorage`.
- Authorization is enforced in the API (`api.py` dependencies `writer`, `coordinator`, `resolver`), not in the frontend. Tests: `test_role_based_access_to_people`, `test_unauthorized_operator_actions`.

## 5. Encryption of sensitive records

- **Algorithm.** AES-256-GCM with a random 96-bit nonce per field (`crypto.encrypt_field`).
- **Binding.** The associated data is `person:<id>:<field>`, so a ciphertext copied into another record or field fails authentication (`test_encrypted_field_bound_to_its_record`).
- **Where.** Encryption happens in `Node.report_person` / `update_person_status` / `add_person_evidence` **before** the event is built. The event schema refuses plaintext in sensitive fields (`events.SensitiveFields`), so plaintext cannot enter the log even through a client bug (`test_plaintext_person_data_cannot_enter_an_event`).
- **Result.** Person details are encrypted at rest on every node, in transit, and on the server. A raw scan of each SQLite file (including the WAL) finds no plaintext name (`test_sensitive_data_encrypted_at_rest_and_opaque_to_server`).
- **Key distribution.** One deployment DEK is generated at provisioning and stored only wrapped (AES-GCM under an scrypt-derived KEK) per operator account that may write people records. Viewers and the server never receive it.
- **What stays plaintext** (needed for merging and coordination): person record id, status, verification state, reporting team, timestamps.

### Honest limits of at-rest protection

- **Coordination data is not encrypted at rest.** Needs, shelters, supplies and tasks are plaintext in SQLite. Full-database encryption (SQLCipher) or OS disk encryption is recommended for real deployments.
- **The key is only as strong as the passphrase.** The demo passphrase `relayaid-demo` is public; it exists only for local demonstration and must be changed (`RELAYAID_OPERATOR_PASSPHRASE`) before provisioning anything real.
- **Device signing keys are not passphrase-protected**, because a node must keep syncing (relaying ciphertext) while no operator is logged in. A thief with the file can impersonate the node to peers until it is removed from the roster. Hardware-backed keystores would fix this.
- **Secure deletion is not guaranteed.** SQLite pages, WAL files and SSD wear-levelling can retain old data; RelayAid never deletes events anyway (append-only). Crypto-shredding (destroying the DEK) is the realistic deletion strategy and is future work.
- **A logged-in, unlocked device is not protected** beyond session expiry.

## 6. Transport security

- **Local MVP exception:** nodes talk plain HTTP on `127.0.0.1`. Integrity and authenticity are still provided by request, response and event signatures, and person fields are encrypted end to end, but **coordination data travels unencrypted**.
- **Required for any real network:** TLS (HTTPS) on every node and the dashboard, ideally mutual TLS pinned to the roster keys. HTTPS is mandatory whenever traffic leaves a single machine or crosses Wi-Fi/mesh links, and whenever the dashboard is accessed from another device (bearer tokens would otherwise be exposed).
- CORS is limited to `localhost` / `127.0.0.1` origins.

## 7. Data minimization

- The person form collects only what is needed to reunite or assist: name, age, description, last-known location, contact. All optional; all encrypted.
- People never appear on the overview; only counts do.
- The audit log records who did what to which id (e.g. `person_reported pr_…`), never content. Decrypted views are audited (`people_records_viewed`).
- Logs: event type and ids only (`test_sensitive_data_not_written_to_logs`).
- The demo dataset uses an obviously fictional person ("Demo Person One").

## 8. Security test coverage

`backend/tests/test_security.py` (17 tests): unauthenticated, unknown, mis-signed, replayed and stale peer requests; tampered events; malformed and malicious envelopes and payloads; plaintext-in-event refusal; role-based access; at-rest encryption and server opacity; log hygiene; brute-force lockout; session expiry dropping keys; node lock; record-bound ciphertext; impostor responses. Allocation integrity tests live in `test_allocation_safety.py`.
