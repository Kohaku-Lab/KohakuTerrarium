"""Laboratory APP extension adapters.

Each adapter binds a local-process subsystem (the terrarium engine,
the session store, the studio identity layer) to an APP namespace
on a Laboratory node (client or host). The adapter's only job is to
translate AppMessages into local Python calls and pack results back.

Adapters live here, not in their subsystem packages, because:

- Single-host mode imports zero Laboratory modules. Adapters live
  under :mod:`kohakuterrarium.laboratory` so the runtime and studio
  packages stay laboratory-free.
- Each adapter is a thin shim — no business logic. Keeping them
  together makes the dispatch surface easy to audit.

Naming convention: ``<NamespaceUnderscored>Adapter`` for the class
binding namespace ``<namespace.dotted>``. E.g.
``TerrariumRuntimeAdapter`` registers ``terrarium.runtime``.
"""

from kohakuterrarium.laboratory.adapters.studio_catalog import (
    StudioCatalogAdapter,
)
from kohakuterrarium.laboratory.adapters.studio_deploy import (
    StudioDeployAdapter,
)
from kohakuterrarium.laboratory.adapters.studio_identity import (
    StudioIdentityAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_attach import (
    TerrariumAttachAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_broadcast import (
    TerrariumBroadcastAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_events import (
    TerrariumEventsAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_files import (
    TerrariumFilesAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_output_wire import (
    TerrariumOutputWireAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_pty import (
    TerrariumPtyAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_runtime import (
    TerrariumRuntimeAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium_session import (
    TerrariumSessionAdapter,
)

__all__ = [
    "StudioCatalogAdapter",
    "StudioDeployAdapter",
    "StudioIdentityAdapter",
    "TerrariumAttachAdapter",
    "TerrariumBroadcastAdapter",
    "TerrariumEventsAdapter",
    "TerrariumFilesAdapter",
    "TerrariumOutputWireAdapter",
    "TerrariumPtyAdapter",
    "TerrariumRuntimeAdapter",
    "TerrariumSessionAdapter",
]
