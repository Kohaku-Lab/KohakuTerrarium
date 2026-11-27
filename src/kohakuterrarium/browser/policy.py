"""Network policy decisions for browser traffic: URLs, host lists, addresses.

Pure decision logic with no I/O. The filtering proxy and the navigate tool
ask a :class:`NetworkPolicy` whether a URL, a host or a resolved address may
be reached; every answer is a :class:`Decision` with a reason code.
"""

import ipaddress
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
ExternalCheck = Callable[[str, int], str | None]

WEB_SCHEMES = {"http": 80, "https": 443}
FLAG_CLASSES = {"loopback": "allow_loopback"} | {
    name: "allow_private" for name in ("private", "cgnat", "unique_local")
}

_V4 = ipaddress.IPv4Network
_V6 = ipaddress.IPv6Network
_IPV4_CLASSES: tuple[tuple[ipaddress.IPv4Network, str], ...] = (
    (_V4("0.0.0.0/8"), "unspecified"),
    (_V4("127.0.0.0/8"), "loopback"),
    (_V4("10.0.0.0/8"), "private"),
    (_V4("172.16.0.0/12"), "private"),
    (_V4("192.168.0.0/16"), "private"),
    (_V4("100.64.0.0/10"), "cgnat"),
    (_V4("169.254.0.0/16"), "link_local"),
    (_V4("192.0.0.0/24"), "reserved"),
    (_V4("224.0.0.0/4"), "multicast"),
    (_V4("240.0.0.0/4"), "reserved"),
)
_IPV6_CLASSES: tuple[tuple[ipaddress.IPv6Network, str], ...] = (
    (_V6("::/128"), "unspecified"),
    (_V6("::1/128"), "loopback"),
    (_V6("fc00::/7"), "unique_local"),
    (_V6("fe80::/10"), "link_local"),
    (_V6("fec0::/10"), "private"),
    (_V6("ff00::/8"), "multicast"),
    (_V6("100::/64"), "reserved"),
    (_V6("2001::/32"), "reserved"),
)
_IPV4_MAPPED = _V6("::ffff:0:0/96")
_IPV4_COMPATIBLE = _V6("::/96")
_NAT64 = _V6("64:ff9b::/96")
_GLOBAL_UNICAST = _V6("2000::/3")
_SIX_TO_FOUR = _V6("2002::/16")

_LABEL = re.compile(r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?|[a-z0-9_]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Decision:
    """The verdict on one URL, host or address, with a reason code and sentence."""

    allowed: bool
    code: str
    reason: str

    def __bool__(self) -> bool:
        return self.allowed

    def __str__(self) -> str:
        return f"{self.code}: {self.reason}"


ALLOWED = Decision(True, "ok", "allowed")


def _deny(code: str, reason: str) -> Decision:
    return Decision(False, code, reason)


def parse_ip(host: str) -> IPAddress | None:
    """Return ``host`` as an IP address when it is a literal, else ``None``."""
    text = host.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def canonical_ip(ip: IPAddress) -> str:
    """The same text for an address on every supported Python, without scope id."""
    if ip.version == 4:
        return str(ip)
    if ip.ipv4_mapped is not None:
        return f"::ffff:{ip.ipv4_mapped}"
    return str(ip).split("%")[0]


def normalize_host(host: str) -> str:
    """Lower-case, strip the trailing dot and IDNA-encode a host name.

    IP literals come back in canonical form without brackets. Raises
    ``ValueError`` for anything that is not a syntactically valid host.
    """
    ip = parse_ip(host)
    if ip is not None:
        return canonical_ip(ip)
    text = host.strip().lower()
    if text.endswith("."):
        text = text[:-1]
    if not text or _CONTROL.search(text):
        raise ValueError(f"invalid host {host!r}")
    try:
        ascii_host = text.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError(f"invalid host {host!r}") from exc
    if not all(_LABEL.fullmatch(label) for label in ascii_host.split(".")):
        raise ValueError(f"invalid host {host!r}")
    return ascii_host


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip in _IPV4_MAPPED or ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _IPV4_COMPATIBLE and int(ip) > 1:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _SIX_TO_FOUR:
        return ip.sixtofour
    return None


def _plain_ip(address: str | IPAddress) -> IPAddress:
    """``address`` as an address object, an IPv4-mapped IPv6 address as its IPv4."""
    ip = address if not isinstance(address, str) else ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def classify_address(address: str | IPAddress) -> str | None:
    """Return the special-use class of an address, or ``None`` when public.

    IPv4 embedded in IPv6 (mapped, compatible, NAT64, 6to4) is judged by the
    embedded IPv4 address. Raises ``ValueError`` for a non-address.
    """
    ip = address if not isinstance(address, str) else ipaddress.ip_address(address)
    if ip.version == 6:
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            return classify_address(embedded)
        for network, name in _IPV6_CLASSES:
            if ip in network:
                return name
        return None if ip in _GLOBAL_UNICAST else "reserved"
    for network, name in _IPV4_CLASSES:
        if ip in network:
            return name
    return None


@dataclass(frozen=True)
class HostPattern:
    """One ``allow_hosts`` / ``block_hosts`` entry: exact or ``*.`` suffix, optional port."""

    host: str
    wildcard: bool = False
    port: int | None = None

    @classmethod
    def parse(cls, text: str) -> "HostPattern":
        """Parse ``example.com``, ``*.example.com``, ``host:port`` or ``[v6]:port``."""
        raw = text.strip()
        host, port = raw, None
        if raw.startswith("["):
            end = raw.find("]")
            if end < 0:
                raise ValueError(f"invalid host pattern {text!r}")
            host, rest = raw[: end + 1], raw[end + 1 :]
            if rest:
                port = _parse_port(rest[1:] if rest.startswith(":") else rest, text)
        elif raw.count(":") == 1:
            host, port_text = raw.split(":")
            port = _parse_port(port_text, text)
        wildcard = host.startswith("*.")
        if wildcard:
            host = host[2:]
        if "*" in host:
            raise ValueError(f"invalid host pattern {text!r}")
        normal = normalize_host(host)
        if wildcard and parse_ip(normal) is not None:
            raise ValueError(f"wildcard over an IP address in {text!r}")
        return cls(normal, wildcard, port)

    @property
    def text(self) -> str:
        """The normalised pattern as it would be written in options."""
        host = f"*.{self.host}" if self.wildcard else self.host
        if self.port is None:
            return host
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{self.port}"

    def matches(self, host: str, port: int) -> bool:
        """Whether ``host:port`` falls under this pattern."""
        if self.port is not None and self.port != port:
            return False
        try:
            normal = normalize_host(host)
        except ValueError:
            return False
        if self.wildcard:
            return normal.endswith("." + self.host)
        return normal == self.host


def _parse_port(text: str, pattern: str) -> int:
    if not text.isdigit() or not 0 < int(text) < 65536:
        raise ValueError(f"invalid port in host pattern {pattern!r}")
    return int(text)


def _patterns(entries: Iterable[str | HostPattern]) -> tuple[HostPattern, ...]:
    return tuple(
        entry if isinstance(entry, HostPattern) else HostPattern.parse(entry)
        for entry in entries
    )


@dataclass(frozen=True)
class NetworkPolicy:
    """Which URLs, hosts and resolved addresses browser traffic may reach.

    ``external_check(host, port)`` returns ``None`` to allow or a reason to
    deny; it is consulted after the host lists. Host patterns are parsed
    and validated at construction.
    """

    allow_hosts: tuple[HostPattern, ...] = ()
    block_hosts: tuple[HostPattern, ...] = ()
    allow_loopback: bool = False
    allow_private: bool = False
    external_check: ExternalCheck | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allow_hosts", _patterns(self.allow_hosts))
        object.__setattr__(self, "block_hosts", _patterns(self.block_hosts))

    def with_external_check(self, check: ExternalCheck | None) -> "NetworkPolicy":
        """A copy of this policy that consults ``check``."""
        return replace(self, external_check=check)

    def check_url(self, url: str) -> Decision:
        """Judge a navigation target: web scheme, well-formed, host lists."""
        if _CONTROL.search(url):
            return _deny("control_char", "the URL contains control characters")
        if url.strip().lower() == "about:blank":
            return ALLOWED
        try:
            parts = urlsplit(url.strip())
            hostname = parts.hostname
        except ValueError:
            return _deny("malformed", "the URL cannot be parsed")
        scheme = parts.scheme.lower()
        if scheme not in WEB_SCHEMES:
            return _deny("scheme", f"only http and https URLs are allowed, not {url!r}")
        if parts.username is not None or parts.password is not None:
            return _deny("credentials", "URLs with embedded credentials are refused")
        try:
            port = parts.port
        except ValueError:
            port = 0
        if port == 0:
            return _deny("malformed", "the URL has an invalid port")
        port = port or WEB_SCHEMES[scheme]
        if not hostname:
            return _deny("malformed", "the URL has no host")
        decision = self.check_host(hostname, port)
        if decision and parse_ip(hostname) is not None:
            return self.check_address(hostname, port)
        return decision

    def check_host(self, host: str, port: int) -> Decision:
        """Judge a host name: ``block_hosts``, then ``allow_hosts``, then the external check."""
        try:
            normal = normalize_host(host)
        except ValueError:
            return _deny("malformed", "the host name is not valid")
        if any(p.matches(normal, port) for p in self.block_hosts):
            return _deny("blocked_host", f"{normal} is in block_hosts")
        if self.allow_hosts and not any(
            p.matches(normal, port) for p in self.allow_hosts
        ):
            return _deny("not_allowed_host", f"{normal} is not in allow_hosts")
        if self.external_check is not None:
            try:
                verdict = self.external_check(normal, port)
            except Exception as exc:
                return _deny("external", f"the external network check failed: {exc}")
            if verdict is not None:
                return _deny("external", f"{normal} denied: {verdict}")
        return ALLOWED

    def check_address(
        self, address: str | IPAddress, port: int | None = None
    ) -> Decision:
        """Judge a resolved address: IP-literal ``block_hosts``, then its special-use class.

        A block pattern with a port applies only when ``port`` is given and
        equal; an IPv4-mapped IPv6 address counts as its IPv4 address.
        """
        try:
            kind = classify_address(address)
        except ValueError:
            return _deny("malformed", f"{address!r} is not an IP address")
        same = _plain_ip(address)
        for pattern in self.block_hosts:
            literal = parse_ip(pattern.host)
            if literal is None or _plain_ip(literal) != same:
                continue
            if pattern.port is None or pattern.port == port:
                return _deny(
                    "blocked_host",
                    f"address {address} is in block_hosts ({pattern.text})",
                )
        if kind is None:
            return ALLOWED
        flag = FLAG_CLASSES.get(kind)
        if flag is not None and getattr(self, flag):
            return ALLOWED
        hint = f" (set {flag} to permit)" if flag else ""
        return _deny(kind, f"address {address} is {kind.replace('_', '-')}{hint}")
