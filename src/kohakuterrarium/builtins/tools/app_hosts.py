"""Host allow-list for instant apps: presets, parsing and validation.

The hosts only widen where an app may load scripts, styles, fonts and
images from; the frame's ``connect-src`` stays ``'none'`` whatever is listed.
"""

import re
from typing import Any

FONTS = ("fonts.googleapis.com", "fonts.gstatic.com")
TAILWIND = ("cdn.tailwindcss.com",)
PRESETS: dict[str, tuple[str, ...]] = {
    "common": (
        "cdn.jsdelivr.net",
        "cdnjs.cloudflare.com",
        "unpkg.com",
        "esm.sh",
        *FONTS,
        *TAILWIND,
    ),
    "fonts": FONTS,
    "tailwind": TAILWIND,
}
DEFAULT_HOSTS = "common"

_HOST = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)


def parse_allowed_hosts(value: Any) -> tuple[str, ...]:
    """Expand a preset name, a host, or a list mixing both into sorted unique hosts.

    ``None`` means the default preset; ``[]`` means no hosts. Raises
    ``ValueError`` for an unknown preset or anything that is not a bare host
    name (no scheme, port, path or wildcard).
    """
    if value is None:
        value = DEFAULT_HOSTS
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValueError("allowed_hosts must be a preset name, a host, or a list")
    hosts: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"invalid allowed_hosts entry: {item!r}")
        name = item.strip().lower()
        if name in PRESETS:
            hosts.update(PRESETS[name])
        elif _HOST.fullmatch(name):
            hosts.add(name)
        else:
            raise ValueError(
                f"allowed_hosts entry {item!r} is neither a preset "
                f"({', '.join(sorted(PRESETS))}) nor a bare host name"
            )
    return tuple(sorted(hosts))
