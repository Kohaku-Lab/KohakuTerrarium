"""Provide password hashing, token hashing, and secure identifier generation.

User-chosen passwords use adaptive bcrypt to resist offline guessing. Random
256-bit tokens use fast SHA3-512 digests because their entropy already prevents
practical brute force. Bcrypt loads lazily so single-tenant Android builds can use
token and session helpers without shipping an unavailable bcrypt wheel.
"""

import hashlib
import secrets


def _bcrypt():
    """Load bcrypt only for password operations and explain unsupported platforms."""
    try:
        import bcrypt
    except ImportError as exc:
        raise RuntimeError(
            "bcrypt is required for password hashing but is not "
            "installed.  On Android the L4 multi-user auth surface "
            "is unavailable by design (the Briefcase/Chaquopy build "
            "strips bcrypt because the Chaquopy index has no wheel "
            "for bcrypt>=4).  Run a non-Android build or install "
            "bcrypt manually if you need password-based auth here."
        ) from exc
    return bcrypt


def hash_password(password: str, rounds: int = 12) -> str:
    """Hash a password with the configurable exponential bcrypt cost factor."""
    bcrypt = _bcrypt()
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a bcrypt password, treating malformed or unavailable hashes as misses.

    Authentication callers intentionally receive ``False`` rather than distinct
    errors so corrupt hashes and unsupported bcrypt platforms do not leak details.
    """
    try:
        bcrypt = _bcrypt()
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError, RuntimeError):
        return False


def generate_token() -> str:
    """Generate a 256-bit hexadecimal token whose plaintext is shown only once."""
    return secrets.token_hex(32)


def generate_session_id() -> str:
    """Generate a compact URL-safe 256-bit identifier for the session cookie."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the one-way SHA3-512 digest stored for API-token lookup."""
    return hashlib.sha3_512(token.encode("utf-8")).hexdigest()


def hash_invitation_token(token: str) -> str:
    """Same hash function as API tokens; named separately for clarity."""
    return hash_token(token)


__all__ = [
    "generate_session_id",
    "generate_token",
    "hash_invitation_token",
    "hash_password",
    "hash_token",
    "verify_password",
]
