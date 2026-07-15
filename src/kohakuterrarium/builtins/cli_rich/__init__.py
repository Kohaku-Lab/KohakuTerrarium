"""Rich CLI mode — inline three-layer model (scrollback / live region / composer).

Exposes the application orchestrator and its input/output modules.
"""

from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
from kohakuterrarium.builtins.cli_rich.input import RichCLIInput
from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput

__all__ = ["RichCLIApp", "RichCLIInput", "RichCLIOutput"]
