"""Cryptographic primitives — bcrypt + SHA3-512 + token generation.

Lifted from KohakuHub's ``auth/utils.py`` and trimmed to KT's needs.

| Primitive | Use | Cost |
|---|---|---|
| bcrypt | Password hash | Adaptive (configurable rounds; default 12) |
| SHA3-512 | API token hash (one-way, fast) | ~constant |
| ``secrets.token_urlsafe(32)`` | Session ID + API token plaintext | 256-bit CSPRNG |

We deliberately use **bcrypt for passwords** and **SHA3-512 for tokens**:

- Passwords are user-chosen, often low-entropy, and verified by a
  human typing them.  Bcrypt's slowness is what frustrates offline
  brute force after a DB leak.
- API tokens are CSPRNG-generated 256-bit secrets.  An attacker can't
  brute-force them in any practical timeframe, so an expensive hash
  buys nothing.  Fast SHA3-512 keeps lookup latency negligible.

Mirror of KohakuHub's choices.

bcrypt is imported lazily because the Android (Briefcase / Chaquopy)
build strips it from the install set — bcrypt 4.x+ is Rust/PyO3 and
the Chaquopy curated index tops out at 3.2.2, while we pin >=4.0.0.
Android is single-tenant (no L4 multi-user auth surface) so the
graceful-unavailable path is the chosen carve-out.  Module import
must still succeed without bcrypt; only the password-hash callsites
raise if bcrypt is missing at the moment of use.
"""

import hashlib
import secrets


def _bcrypt():
    """Resolve the :mod:`bcrypt` module, raising a clear error if absent.

    Deferred so that ``from kohakuterrarium.api.auth.crypto import ...``
    works on platforms (Android Chaquopy) where bcrypt is stripped at
    build time.  Only ``hash_password`` / ``verify_password`` call this
    — token-hash and session-id helpers stay bcrypt-free and remain
    fully functional everywhere.
    """
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
    """Hash a user-supplied password with bcrypt.

    The ``rounds`` cost factor is configurable via
    :attr:`AuthConfig.bcrypt_rounds` so operators on slow hardware can
    dial it back (bcrypt cost is per-hash, exponential).  Default 12
    is bcrypt's modern recommendation (~250ms on commodity hardware
    in 2026).
    """
    bcrypt = _bcrypt()
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish bcrypt verify.

    Returns ``False`` on any error (corrupt hash, wrong format,
    bcrypt not installed on this platform) rather than raising —
    the auth path's miss handler treats them as "wrong creds, 401."
    Android builds strip bcrypt, so calls here on Android will
    silently fail (and L4 auth routes return 401), which matches
    the documented "no L4 on Android" behaviour.
    """
    try:
        bcrypt = _bcrypt()
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError, RuntimeError):
        return False


def generate_token() -> str:
    """Generate a random 256-bit token, hex-encoded (64 chars).

    Used for the plaintext API token shown to the user once at
    creation time.  The DB stores only :func:`hash_token` of this
    value.
    """
    return secrets.token_hex(32)


def generate_session_id() -> str:
    """Generate a URL-safe random session identifier.

    Used as the HttpOnly cookie value.  ``token_urlsafe(32)`` gives
    256 bits of entropy in a 43-char base64url string — fits
    comfortably in a cookie without taking the full 4KB budget.
    """
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA3-512 hex digest.  Used for API token lookup.

    Fast (one-shot), one-way, collision-resistant.  An attacker who
    grabs the DB cannot recover plaintext tokens.
    """
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
