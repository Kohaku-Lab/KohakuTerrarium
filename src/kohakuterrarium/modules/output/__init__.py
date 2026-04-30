"""
Output module - route and deliver agent output.

Base classes and protocols are defined here.
Implementations are in kohakuterrarium.builtins.outputs.

Exports:
- OutputModule: Protocol for output modules
- BaseOutputModule: Base class for output modules
- OutputRouter: Routes parse events to outputs
"""

from kohakuterrarium.modules.output.base import BaseOutputModule, OutputModule
from kohakuterrarium.modules.output.router import OutputRouter, OutputState
from kohakuterrarium.modules.output.router_multi import MultiOutputRouter

__all__ = [
    # Protocol and base
    "OutputModule",
    "BaseOutputModule",
    # Router
    "OutputRouter",
    "MultiOutputRouter",
    "OutputState",
]
