"""OIDC id-token claim decoding for Codex tokens.

Split out of :mod:`kohakuterrarium.llm.codex_auth` (at the file-size cap):
the JWT payload decode has no dependency on the OAuth flow, so it lives
in its own module and is imported back.
"""

import base64
import json

# OIDC claim namespace carrying the ChatGPT account id in the id-token.
ID_TOKEN_AUTH_CLAIM = "https://api.openai.com/auth"


def account_id_from_id_token(id_token: str) -> str:
    """Derive the ChatGPT account id from an OIDC id-token JWT.

    The id-token's ``https://api.openai.com/auth`` claim carries
    ``chatgpt_account_id`` — the value the Codex backend expects in the
    ``ChatGPT-Account-Id`` header (see codex-rs ``token_data.rs``).
    Returns ``""`` for an absent or malformed token; callers treat an
    empty id as "unknown" rather than an error.
    """
    if not id_token:
        return ""
    try:
        payload_b64 = id_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        )
    except Exception:
        return ""
    auth = payload.get(ID_TOKEN_AUTH_CLAIM)
    account_id = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    return account_id if isinstance(account_id, str) else ""
