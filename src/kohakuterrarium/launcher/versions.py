"""Order release version strings without third-party packages.

Handles the shapes releases use: ``2.2.1``, ``2.2.1rc1`` and
``2.2.1.dev20261031120000+abc123``. A development build sorts before its
pre-releases, which sort before the final release.
"""

import re

_VERSION = re.compile(
    r"^v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:\.?(?P<pre>a|b|rc)(?P<pre_n>\d+))?"
    r"(?:\.?dev(?P<dev>\d+))?"
)
_PRE_RANK = {"a": 1, "b": 2, "rc": 3}
_FINAL_RANK = 4


def version_key(version: str) -> tuple | None:
    """Return a sortable key for ``version``, or ``None`` when it is not parseable."""
    match = _VERSION.match(version.split("+", 1)[0].strip())
    if match is None:
        return None
    release = [int(part) for part in match["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    if match["pre"]:
        rank, number = _PRE_RANK[match["pre"]], int(match["pre_n"])
    elif match["dev"] is not None:
        rank, number = 0, int(match["dev"])
    else:
        rank, number = _FINAL_RANK, 0
    return (tuple(release), rank, number)


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is strictly newer; unparseable versions never are."""
    a, b = version_key(candidate), version_key(current)
    return a is not None and b is not None and a > b


__all__ = ["is_newer", "version_key"]
