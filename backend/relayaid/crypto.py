"""Cryptographic primitives used by RelayAid.

* Ed25519 device identities: every node signs the events it originates and every
  peer-to-peer sync request/response. Receivers verify against the deployment roster.
* AES-256-GCM: sensitive person-record fields are encrypted with a deployment data key
  (DEK) before they ever become part of an event, so they are encrypted at rest on every
  node and in transit through peers and the central server.
* scrypt: the DEK is stored on disk only in wrapped (encrypted) form, under a key derived
  from an operator passphrase. A node that is powered on but locked holds no usable DEK.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

CIPHER_PREFIX = "enc:v1:"


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def canonical(obj: Any) -> bytes:
    """Deterministic JSON encoding used for signatures and state digests."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- identities
def generate_signing_key() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) for a new Ed25519 device identity."""
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    pub_raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64e(priv_raw), b64e(pub_raw)


class Signer:
    def __init__(self, private_key_b64: str):
        self._key = Ed25519PrivateKey.from_private_bytes(b64d(private_key_b64))

    def sign(self, data: bytes) -> str:
        return b64e(self._key.sign(data))


def verify_signature(public_key_b64: str, data: bytes, signature_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(b64d(public_key_b64)).verify(b64d(signature_b64), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- AEAD
def generate_data_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def encrypt_field(key: bytes, plaintext: str, aad: str) -> str:
    """Encrypt one sensitive value. `aad` binds the ciphertext to its record so a
    ciphertext cannot be cut-and-pasted into a different record undetected."""
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    return CIPHER_PREFIX + b64e(nonce + ct)


def decrypt_field(key: bytes, token: str, aad: str) -> str:
    if not token.startswith(CIPHER_PREFIX):
        raise ValueError("not an encrypted field")
    raw = b64d(token[len(CIPHER_PREFIX):])
    try:
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad.encode("utf-8")).decode("utf-8")
    except InvalidTag as exc:  # wrong key or tampered ciphertext
        raise ValueError("decryption failed") from exc


def is_ciphertext(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(CIPHER_PREFIX)


# --------------------------------------------------------------------------- passphrases
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(passphrase.encode("utf-8"))


def hash_passphrase(passphrase: str) -> str:
    salt = os.urandom(16)
    return f"scrypt${b64e(salt)}${b64e(derive_key(passphrase, salt))}"


def check_passphrase(passphrase: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        return hmac.compare_digest(derive_key(passphrase, b64d(salt)), b64d(digest))
    except (ValueError, TypeError):
        return False


def wrap_key(passphrase: str, key: bytes) -> dict[str, str]:
    """Encrypt `key` under a passphrase-derived key-encryption key."""
    salt = os.urandom(16)
    kek = derive_key(passphrase, salt)
    nonce = os.urandom(12)
    return {"salt": b64e(salt), "blob": b64e(nonce + AESGCM(kek).encrypt(nonce, key, b"relayaid-dek"))}


def unwrap_key(passphrase: str, wrapped: dict[str, str]) -> bytes:
    kek = derive_key(passphrase, b64d(wrapped["salt"]))
    raw = b64d(wrapped["blob"])
    try:
        return AESGCM(kek).decrypt(raw[:12], raw[12:], b"relayaid-dek")
    except InvalidTag as exc:
        raise ValueError("wrong passphrase") from exc
