"""Studio tier — programmatic façade over the studio sub-packages.

The :class:`Studio` class wraps a :class:`Terrarium` engine and
exposes catalog / identity / sessions / persistence / editors /
attach as nested namespaces.  See
``plans/structure-hierarchy/studio-class/design.md``.
"""

from kohakuterrarium.studio.studio import Studio

__all__ = ["Studio"]
