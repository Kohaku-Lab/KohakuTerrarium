"""Serving — static frontend launcher.

The legacy ``KohakuManager`` facade and the ``AgentSession`` /
``StreamOutput`` / ``ChannelEvent`` helpers were removed in Phase 3
of the studio cleanup. The :class:`Terrarium` engine
(``kohakuterrarium.terrarium``) and the studio sessions modules
(``kohakuterrarium.studio.sessions``) own those responsibilities now.
This package keeps only :mod:`kohakuterrarium.serving.web`, the
static-files launcher used by ``kt serve`` and the desktop bundle.
"""

__all__: list[str] = []
