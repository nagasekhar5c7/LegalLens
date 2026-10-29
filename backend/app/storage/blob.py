"""Encrypted object store. NFR-9: encryption at rest for files, extracted text
and (later) embeddings.

Extracted page text goes through here too, which is the part that is easy to
forget: a page's text is as privileged as the PDF it came from (DR-5).

Layout is content-addressed by SHA-256 of the *plaintext*, so an identical
upload dedupes (FR-1) without decrypting anything to compare.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

NONCE_BYTES = 12
MAGIC = b"LL1"  # version prefix, so a future key rotation can tell formats apart


class BlobDecryptError(RuntimeError):
    """Raised when a stored object fails authentication — a wrong key or a
    tampered file. Never swallowed: a silently-wrong decrypt would surface as
    garbled document text, which looks like a parser bug."""


def _aesgcm() -> AESGCM:
    key_hex = get_settings().blob_key
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise RuntimeError("LL_BLOB_KEY must be hex-encoded") from exc
    if len(key) != 32:
        raise RuntimeError("LL_BLOB_KEY must be 32 bytes (64 hex chars) for AES-256")
    return AESGCM(key)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _path_for(key: str) -> Path:
    root = get_settings().blobs_dir
    # Two-level fan-out keeps directory listings sane at 50 docs × 500 pages.
    safe = key.replace("..", "").strip("/")
    return root / safe


def object_key(kind: str, document_id: str, name: str) -> str:
    """Keys are namespaced by kind so a hard delete can sweep a prefix."""
    return f"{kind}/{document_id}/{name}.enc"


def write(key: str, plaintext: bytes, *, aad: str | None = None) -> str:
    """Encrypt and store.

    The AAD defaults to the object key, which binds the ciphertext to its
    location: keys embed the document id and the page number
    (`pages/{doc}/p{n}.enc`), so an attacker who can move files cannot swap one
    page's text for another's. Callers should leave `aad` alone — passing a
    custom value on the write and forgetting it on the read is an
    authentication failure that surfaces as unreadable document text.
    """
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(NONCE_BYTES)
    ct = _aesgcm().encrypt(nonce, plaintext, (aad or key).encode())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(MAGIC + nonce + ct)
    tmp.replace(path)  # atomic, so a crash never leaves a half-written blob
    return key


def read(key: str, *, aad: str | None = None) -> bytes:
    path = _path_for(key)
    if not path.exists():
        raise FileNotFoundError(key)
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise BlobDecryptError(f"{key}: unrecognised blob format")
    nonce = raw[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
    ct = raw[len(MAGIC) + NONCE_BYTES :]
    try:
        return _aesgcm().decrypt(nonce, ct, (aad or key).encode())
    except InvalidTag as exc:
        raise BlobDecryptError(f"{key}: authentication failed") from exc


def write_text(key: str, text: str, *, aad: str | None = None) -> str:
    return write(key, text.encode("utf-8"), aad=aad)


def read_text(key: str, *, aad: str | None = None) -> str:
    return read(key, aad=aad).decode("utf-8")


def exists(key: str) -> bool:
    return _path_for(key).exists()


def delete(key: str) -> bool:
    """FR-26: real removal, reported honestly — the caller logs what it deleted."""
    path = _path_for(key)
    if not path.exists():
        return False
    path.unlink()
    return True


def delete_prefix(prefix: str) -> int:
    """Sweep every object under a prefix. Used by the hard-delete worker so a
    page image nobody recorded a key for still goes."""
    root = _path_for(prefix)
    if not root.exists():
        return 0
    count = sum(1 for p in root.rglob("*") if p.is_file())
    shutil.rmtree(root, ignore_errors=True)
    return count


def orphan_sweep(known_prefixes: set[str]) -> int:
    """Reconciliation: anything under blobs/ whose document prefix is not in the
    database is an orphan from an interrupted delete. Returns the count removed."""
    root = get_settings().blobs_dir
    if not root.exists():
        return 0
    removed = 0
    for kind_dir in root.iterdir():
        if not kind_dir.is_dir():
            continue
        for doc_dir in kind_dir.iterdir():
            if doc_dir.is_dir() and f"{kind_dir.name}/{doc_dir.name}" not in known_prefixes:
                removed += sum(1 for p in doc_dir.rglob("*") if p.is_file())
                shutil.rmtree(doc_dir, ignore_errors=True)
    return removed
