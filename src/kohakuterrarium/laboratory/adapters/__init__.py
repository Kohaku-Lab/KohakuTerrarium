"""Expose adapters between Laboratory APP namespaces and local subsystems.

Adapters remain under :mod:`kohakuterrarium.laboratory` so local-only runtime
and studio code does not depend on Laboratory. They translate APP messages at
the transport boundary and intentionally contain no subsystem business logic.
"""

from kohakuterrarium.laboratory.adapters.studio.catalog import (
    StudioCatalogAdapter,
)
from kohakuterrarium.laboratory.adapters.studio.deploy import (
    StudioDeployAdapter,
)
from kohakuterrarium.laboratory.adapters.studio.identity import (
    StudioIdentityAdapter,
)
from kohakuterrarium.laboratory.adapters.studio.settings import (
    StudioSettingsAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.attach import (
    TerrariumAttachAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.broadcast import (
    TerrariumBroadcastAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.events import (
    TerrariumEventsAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.files import (
    TerrariumFilesAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.output_wire import (
    TerrariumOutputWireAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.pty import (
    TerrariumPtyAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.runtime import (
    TerrariumRuntimeAdapter,
)
from kohakuterrarium.laboratory.adapters.terrarium.session import (
    TerrariumSessionAdapter,
)

__all__ = [
    "StudioCatalogAdapter",
    "StudioDeployAdapter",
    "StudioIdentityAdapter",
    "StudioSettingsAdapter",
    "TerrariumAttachAdapter",
    "TerrariumBroadcastAdapter",
    "TerrariumEventsAdapter",
    "TerrariumFilesAdapter",
    "TerrariumOutputWireAdapter",
    "TerrariumPtyAdapter",
    "TerrariumRuntimeAdapter",
    "TerrariumSessionAdapter",
]
