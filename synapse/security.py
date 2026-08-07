"""Security: Argon2id hashing, service secrets, constant-time comparison.

The Argon2id parameters are fixed by the specification (64 MiB, 3
iterations, parallelism 1) and are not configurable: they are part
of the security contract.

Le module expose un hacheur unique ; les tests peuvent le remplacer par un
fast hasher (``security.install_fast_hasher()``) to speed up the test suite,
while a dedicated test verifies the production parameters.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Parameters imposed by the specification: 64 MiB memory, 3 iterations,
# parallelism 1, Argon2id.
ARGON2_TIME_COST = 3
ARGON2_MEMORY_KIB = 64 * 1024  # 64 MiB
ARGON2_PARALLELISM = 1
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

_PRODUCTION_HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
)

# Hacheur actif. Les tests remplacent ``_hasher`` par une instance rapide.
_hasher = _PRODUCTION_HASHER

# Bounds the number of simultaneous Argon2id computations (~2 × cores). Each
# verification allocates 64 MiB: without this bound, 64 concurrent connections
# would consume up to 4 GiB. Argon2id scaling is memory-hard
# (memory bandwidth saturates well before the core count): limiting
# concurrency therefore does not reduce throughput, but bounds memory under
# load. Excess computations wait their turn (queue).
_ARGON2_SLOTS = max(2, (os.cpu_count() or 2) * 2)
_argon2_slots = threading.BoundedSemaphore(_ARGON2_SLOTS)


@contextmanager
def argon2_slot() -> Iterator[None]:
    """Reserves an Argon2id computation slot (64 MiB memory) for the duration of a
    verification or a hash. Always released, even on error."""
    _argon2_slots.acquire()
    try:
        yield
    finally:
        _argon2_slots.release()


def production_params_ok() -> bool:
    """True if the active hasher uses the production parameters."""
    h = _hasher
    return (
        h.time_cost == ARGON2_TIME_COST
        and h.memory_cost == ARGON2_MEMORY_KIB
        and h.parallelism == ARGON2_PARALLELISM
        and h.type.name == "ID"
    )


def install_fast_hasher() -> None:
    """Replaces the hasher with a fast instance (tests only)."""
    global _hasher
    _hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=32, salt_len=16)


def install_production_hasher() -> None:
    """Restores the production hasher."""
    global _hasher
    _hasher = _PRODUCTION_HASHER


def hash_password(password: str) -> str:
    """Hashes a password with Argon2id and a unique random salt."""
    with argon2_slot():
        return _hasher.hash(password)


def human_password_sentinel() -> str:
    """Hash sentinelle du compte humain (SPEC-WEB §5.2).

    Le mot de passe de l'humain est celui de SON organisation (jamais
    copied): the hash stored on the human account is never verified.
    The sentinel is a valid Argon2id hash of a random secret — in the
    format expected by all checks, with no relation to any
    real password.
    """
    with argon2_slot():
        return _hasher.hash(secrets.token_hex(32))


def verify_password(password_hash: str, password: str) -> bool:
    """Verifies a password. Returns False on any failure, without detail."""
    try:
        with argon2_slot():
            return _hasher.verify(password_hash, password)
    except (VerificationError, VerifyMismatchError, InvalidHashError):
        return False


# "Decoy" hash used to equalize response time when the
# account does not exist (avoids timing-based enumeration).
_DUMMY_HASH: str | None = None


def dummy_hash() -> str:
    """Returns (and caches) a valid Argon2id hash of a dummy password."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(24))
    return _DUMMY_HASH


def verify_dummy(password: str) -> bool:
    """Runs a decoy verification (constant timing)."""
    return verify_password(dummy_hash(), password)


# ---------------------------------------------------------------------------
# Service keys
# ---------------------------------------------------------------------------


def load_or_create_key(path: str | Path, bits: int = 256) -> bytes:
    """Loads a random key from a file, or creates it (0600).

    Used for cursor signing and for encrypting
    backups. The key lives outside the data it protects. If two
    processes create the key simultaneously, the second reads the first one's
    (both get the same key).
    """
    key_path = Path(path)
    if key_path.exists():
        data = key_path.read_bytes()
        if len(data) != bits // 8:
            raise ValueError(f"Invalid key (unexpected size): {key_path}")
        return data
    key = secrets.token_bytes(bits // 8)
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Creation race: the other process won, reuse its key.
        data = key_path.read_bytes()
        if len(data) != bits // 8:
            raise ValueError(f"Invalid key (unexpected size): {key_path}")
        return data
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def constant_time_equals(a: bytes, b: bytes) -> bool:
    """Constant-time comparison."""
    return hmac.compare_digest(a, b)
