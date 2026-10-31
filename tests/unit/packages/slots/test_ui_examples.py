"""The shipped UI extension examples stay valid and keep their paired ids in step."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import yaml

from kohakuterrarium.packages.slots.ui import parse_ui_slot

EXAMPLES = Path(__file__).resolve().parents[4] / "examples" / "ui-extensions"


def _load_plugin(root: Path):
    spec = importlib.util.spec_from_file_location(
        "poll_block_example", root / "poll_block" / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _agent(renderers: dict) -> SimpleNamespace:
    router = SimpleNamespace(ui_renderers=lambda: renderers)
    return SimpleNamespace(output_router=router)


class TestPollBlock:
    root = EXAMPLES / "poll-block"

    def _record(self) -> dict:
        record = yaml.safe_load((self.root / "kohaku.yaml").read_text("utf-8"))
        record["path"] = str(self.root)
        return record

    def test_manifest_ui_block_is_valid(self):
        slot = parse_ui_slot(self._record())
        assert slot.error == ""
        (contribution,) = slot.contributions
        assert contribution.kind == "message-renderer"
        assert contribution.owner == "package:poll-block"
        assert (self.root / "ui" / "index.js").is_file()

    def test_plugin_and_manifest_name_the_same_renderer(self):
        module = _load_plugin(self.root)
        slot = parse_ui_slot(self._record())
        (contribution,) = slot.contributions
        assert module.PollBlockPlugin.prompt_renderers == {contribution.renderer}
        assert f"```{contribution.renderer}" in module.PROMPT

    def test_prompt_part_appears_only_with_the_renderer_attached(self):
        module = _load_plugin(self.root)
        plugin = module.PollBlockPlugin()
        attached = SimpleNamespace(host_agent=_agent({module.RENDERER: 1}))
        absent = SimpleNamespace(host_agent=_agent({"kt.ui": 1}))
        assert plugin.presentation_contribution(attached) == module.PROMPT
        assert plugin.presentation_contribution(absent) is None
