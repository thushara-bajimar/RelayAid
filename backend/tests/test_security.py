"""Security and privacy tests."""
import copy
import gzip
import json
import logging
import time

from relayaid import crypto
from relayaid.security import sign_request
from relayaid.sync import encode

SECRET_NAME = "Meera Nandakumar"


def _peer_post(c, target, sender, path, obj, tamper=None):
    body, _ = encode(obj)
    headers = sign_request(c.node(sender).signer, sender, "POST", path, body)
    if tamper:
        tamper(headers)
    return c.clients[target].post(path, content=body, headers=headers), headers, body


def test_unauthenticated_peer_request_rejected(cluster):
    body, _ = encode({"vv": {}})
    r = cluster.clients["alpha"].post("/peer/pull", content=body)
    assert r.status_code == 401


def test_unknown_peer_rejected(cluster):
    r, _, _ = _peer_post(cluster, "alpha", "bravo", "/peer/pull", {"vv": {}},
                         tamper=lambda h: h.update({"X-RelayAid-Node": "mallory"}))
    assert r.status_code == 403


def test_bad_signature_rejected(cluster):
    def swap(h):
        h["X-RelayAid-Node"] = "charlie"  # signed by bravo, claims to be charlie
    r, _, _ = _peer_post(cluster, "alpha", "bravo", "/peer/pull", {"vv": {}}, tamper=swap)
    assert r.status_code == 403


def test_replayed_request_rejected(cluster):
    c = cluster
    r1, headers, body = _peer_post(c, "alpha", "bravo", "/peer/handshake", {"vv": {}})
    assert r1.status_code == 200
    r2 = c.clients["alpha"].post("/peer/handshake", content=body, headers=headers)
    assert r2.status_code == 409


def test_stale_timestamp_rejected(cluster):
    c = cluster
    body, _ = encode({"vv": {}})
    headers = sign_request(c.node("bravo").signer, "bravo", "POST", "/peer/pull", body)
    old = str(int((time.time() - 3600) * 1000))
    # re-sign with an old timestamp so only the window check can catch it
    from relayaid.security import _string_to_sign
    headers["X-RelayAid-Timestamp"] = old
    headers["X-RelayAid-Signature"] = c.node("bravo").signer.sign(
        _string_to_sign("POST", "/peer/pull", old, headers["X-RelayAid-Nonce"], body))
    assert c.clients["alpha"].post("/peer/pull", content=body, headers=headers).status_code == 401


def test_tampered_event_rejected(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/needs", json={"category": "water", "description": "10 litres", "quantity": 10})
    ev = copy.deepcopy(next(iter(c.node("alpha")._envelopes.values())))
    ev["payload"]["quantity"] = 10_000  # relay tampers with the content
    ack = c.node("bravo").ingest([ev], received_from="charlie")
    assert ack["accepted"] == [] and "signature" in ack["rejected"][0]["reason"]


def test_malicious_payloads_rejected(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/needs", json={"category": "water", "description": "x"})
    good = next(iter(c.node("alpha")._envelopes.values()))
    bad_cases = [
        {**good, "type": "need.dropped_table"},
        {**good, "origin": "mallory"},
        {**good, "seq": -1},
        {k: v for k, v in good.items() if k != "sig"},
        "not even an object",
    ]
    ack = c.node("bravo").ingest(bad_cases, received_from="charlie")
    assert ack["accepted"] == [] and len(ack["rejected"]) == len(bad_cases)
    # API-level validation
    r = c.api("alpha", "POST", "/api/needs", json={"category": "lava", "description": "x"})
    assert r.status_code == 422
    r = c.api("alpha", "POST", "/api/needs", json={"category": "water", "description": "x" * 5000})
    assert r.status_code == 422


def test_plaintext_person_data_cannot_enter_an_event(cluster):
    from relayaid import events as E
    try:
        E.validate_payload("person.reported", "person", {"status": "missing", "reporting_team": "",
                                                         "sensitive": {"name": "plain text"}})
        raise AssertionError("plaintext accepted")
    except E.EventError as exc:
        assert "encrypted" in str(exc)


def test_role_based_access_to_people(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/people", json={"name": SECRET_NAME, "age": "34", "status": "missing",
                                                "last_known_location": "Bridge road"})
    assert c.api("alpha", "GET", "/api/people", user="viewer").status_code == 403
    redacted = c.ok("alpha", "GET", "/api/people", user="responder")[0]
    assert redacted["details"] is None and redacted["status"] == "missing"
    full = c.ok("alpha", "GET", "/api/people")[0]
    assert full["details"]["name"] == SECRET_NAME
    assert c.api("alpha", "POST", "/api/people", user="viewer", json={"name": "x"}).status_code == 403
    # people never show up in the public overview or the event feed in readable form
    feed = json.dumps(c.ok("alpha", "GET", "/api/events"))
    assert SECRET_NAME not in feed and "[encrypted]" in feed
    assert SECRET_NAME not in json.dumps(c.ok("alpha", "GET", "/api/node"))


def test_sensitive_data_encrypted_at_rest_and_opaque_to_server(cluster):
    c = cluster
    c.ok("alpha", "POST", "/api/people", json={"name": SECRET_NAME, "status": "missing"})
    c.sync("alpha", "hub")
    c.sync("bravo", "hub")
    for n in ("alpha", "hub", "bravo"):
        c.node(n).db.conn.execute("PRAGMA wal_checkpoint(FULL)")
        raw = open(c.configs[n].db_path, "rb").read()
        assert SECRET_NAME.encode() not in raw
    # the hub relays the record but holds no key and no operator account
    assert c.node("hub").event_count() >= 1
    assert c.clients["hub"].post("/api/auth/login", json={"username": "coordinator",
                                                          "passphrase": "test-passphrase"}).status_code == 403
    # bravo can decrypt because its coordinator unwrapped the deployment key at login
    assert c.ok("bravo", "GET", "/api/people")[0]["details"]["name"] == SECRET_NAME


def test_sensitive_data_not_written_to_logs(cluster, caplog):
    c = cluster
    with caplog.at_level(logging.DEBUG):
        pid = c.ok("alpha", "POST", "/api/people", json={"name": SECRET_NAME, "status": "missing"})["person_id"]
        c.ok("alpha", "POST", f"/api/people/{pid}/evidence", json={"text": f"{SECRET_NAME} seen near river"})
        c.sync("bravo", "alpha")
    assert SECRET_NAME not in caplog.text
    audit = json.dumps(c.ok("alpha", "GET", "/api/audit"))
    assert SECRET_NAME not in audit and "person_reported" in audit


def test_wrong_passphrase_and_lockout(cluster):
    c = cluster
    for _ in range(5):
        r = c.clients["alpha"].post("/api/auth/login", json={"username": "responder", "passphrase": "nope"})
        assert r.status_code == 401
    r = c.clients["alpha"].post("/api/auth/login", json={"username": "responder", "passphrase": "test-passphrase"})
    assert r.status_code == 429


def test_session_expiry_locks_keys(cluster):
    c = cluster
    hdr = c.login("alpha")
    token = hdr["Authorization"].split()[1]
    s = c.node("alpha").sessions.get(token)
    assert s.dek is not None
    s.expires_at = time.time() - 1
    r = c.clients["alpha"].get("/api/people", headers=hdr)
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    assert s.dek is None


def test_lock_endpoint_ends_sessions(cluster):
    c = cluster
    hdr = c.login("alpha")
    c.ok("alpha", "POST", "/api/lock")
    assert c.clients["alpha"].get("/api/auth/me", headers=hdr).status_code == 401


def test_unauthorized_operator_actions(cluster):
    c = cluster
    assert c.clients["alpha"].get("/api/needs").status_code == 401
    assert c.api("alpha", "PUT", "/api/sim", user="responder", json={"offline": True}).status_code == 403
    assert c.api("alpha", "POST", "/api/needs", user="viewer",
                 json={"category": "water", "description": "x"}).status_code == 403


def test_encrypted_field_bound_to_its_record():
    key = crypto.generate_data_key()
    token = crypto.encrypt_field(key, "secret", "person:pr_1:name")
    assert crypto.decrypt_field(key, token, "person:pr_1:name") == "secret"
    try:
        crypto.decrypt_field(key, token, "person:pr_2:name")  # cut-and-paste into another record
        raise AssertionError("ciphertext accepted for a different record")
    except ValueError:
        pass


def test_response_from_impostor_rejected(cluster):
    """Mutual authentication: a response not signed by the expected peer is refused."""
    c = cluster
    real_post = c.transport.post

    def mitm(base_url, path, body, headers):
        status, rh, rb = real_post(base_url, path, body, headers)
        data = json.loads(gzip.decompress(rb))
        data["vv"] = {"alpha": 999}
        return status, rh, gzip.compress(json.dumps(data).encode())

    c.transport.post = mitm
    r = c.sync("bravo", "alpha")
    c.transport.post = real_post
    assert r["status"] == "failed" and "signature" in r["error"]
