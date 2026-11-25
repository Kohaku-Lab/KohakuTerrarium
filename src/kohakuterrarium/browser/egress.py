"""Where a navigation went, as the filtering proxy records it, and why a hop was refused."""

from urllib.parse import urlsplit

from kohakuterrarium.browser.policy import (
    WEB_SCHEMES,
    NetworkPolicy,
    normalize_host,
    parse_ip,
)
from kohakuterrarium.browser.proxy import EgressRecord

UNREACHABLE = {"upstream": "connect to", "resolve_failed": "resolve"}


def endpoint_of(url: str) -> tuple[str, int]:
    """``(host, port)`` of ``url`` as the proxy records them; ``("", 0)`` when none."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or WEB_SCHEMES.get(parts.scheme.lower(), 0)
        return (normalize_host(host), port) if host else ("", 0)
    except ValueError:
        return ("", 0)


def blocked_reason(record: EgressRecord, policy: NetworkPolicy) -> str:
    """The policy's sentence for a blocked proxy decision, falling back to its code."""
    address = record.address
    if address is None and parse_ip(record.host) is not None:
        address = record.host
    decision = policy.check_host(record.host, record.port)
    if decision and address is not None:
        decision = policy.check_address(address, record.port)
    return decision.reason if not decision.allowed else record.code


def blocked_hops(records: list[EgressRecord], urls: list[str]) -> list[EgressRecord]:
    """The blocked ``records`` whose host and port are those of one of ``urls``."""
    chain = {hop for hop in map(endpoint_of, urls) if hop[0]}
    return [r for r in records if r.blocked and (r.host, r.port) in chain]


def hop_note(record: EgressRecord, target: str) -> str:
    """`` (it led to …)`` when ``record`` is not the target's own endpoint, else empty."""
    start = endpoint_of(target)
    if (record.host, record.port) == start:
        return ""
    if record.host == start[0]:
        return f" (it led to {record.host}:{record.port})"
    return f" (it led to {record.host})"


def unreachable(records: list[EgressRecord], url: str, code: str | None) -> str:
    """``could not connect to <host>`` when the proxy recorded ``code`` for ``url``; else empty."""
    host, port = endpoint_of(url)
    verb = UNREACHABLE.get(code or "")
    for record in records:
        if verb and (record.host, record.port) == (host, port) and record.code == code:
            return f"could not {verb} {host}"
    return ""
