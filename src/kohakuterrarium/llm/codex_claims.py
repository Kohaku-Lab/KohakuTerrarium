"""OIDC id-token claim decoding for Codex tokens.

Decode account claims from Codex OAuth tokens.
"""

import base64
import json

# OpenAI places the ChatGPT account id under this namespaced OIDC claim.
ID_TOKEN_AUTH_CLAIM = "https://api.openai.com/auth"


def account_id_from_id_token(id_token: str) -> str:
    """Extract the ChatGPT account id, returning an empty string if unavailable."""
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
