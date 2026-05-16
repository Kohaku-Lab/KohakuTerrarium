"""Internal implementation of the Laboratory layer (L1–L3).

User code MUST NOT import from this package. The public API surface is
exported from :mod:`kohakuterrarium.laboratory` (L4 only).

L1 transport, L2 framing, and L3 coordination live here. L4 verbs are
in :mod:`kohakuterrarium.laboratory.verbs` and re-exported from the
top-level package.
"""
