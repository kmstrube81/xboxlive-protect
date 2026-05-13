"""argon2id password hashing wrappers (see DESIGN.md §6.3)."""

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from xblp_api.config import Settings


def _make_hasher(settings: Settings) -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost,
        parallelism=settings.argon2_parallelism,
    )


def hash_password(password: str, settings: Settings) -> str:
    return _make_hasher(settings).hash(password)


def verify_password(password_hash: str, password: str, settings: Settings) -> bool:
    """Return True if *password* matches *password_hash*, False if wrong password.

    Raises argon2.exceptions.VerificationError for malformed hashes (not a
    normal code path — only happens if the DB row is corrupt).
    """
    try:
        _make_hasher(settings).verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except VerificationError:
        raise


def needs_rehash(password_hash: str, settings: Settings) -> bool:
    """Return True if the stored hash was produced with different parameters."""
    return _make_hasher(settings).check_needs_rehash(password_hash)
