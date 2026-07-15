"""Expose adapters between Laboratory APP namespaces and local subsystems.

Adapters remain under :mod:`kohakuterrarium.laboratory` so local-only runtime
and studio code does not depend on Laboratory. They translate APP messages at
the transport boundary and intentionally contain no subsystem business logic.
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
from kohakuterrarium.laboratory.adapters.studio_settings import (
    StudioSettingsAdapter,
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
