"""Unit guard for B-iw2-2 (FIXED): ``disable_provider_tools`` config parsing.

``core/config.py`` ``_construct_agent_config`` must carry every
documented top-level key onto the ``AgentConfig`` dataclass. The
``disable_provider_tools`` list (a documented dataclass field, consumed
by ``bootstrap/agent_init._auto_inject_provider_native_tools`` as the
provider-native-tool opt-out) used to be dropped on the floor — this
test is the regression guard for the fix.
"""

from kohakuterrarium.core.config import load_agent_config


def test_disable_provider_tools_key_is_parsed_onto_config(tmp_path):
    """Contract: a creature config with ``disable_provider_tools:`` round
    -trips that list onto ``AgentConfig.disable_provider_tools`` — the
    opt-out the auto-injection path reads."""
    cfg = tmp_path / "ag"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(
        "name: ag\nsystem_prompt: hi\n"
        "disable_provider_tools:\n  - web_search\n  - image_gen\n",
        encoding="utf-8",
    )
    config = load_agent_config(str(cfg))
    assert config.disable_provider_tools == ["web_search", "image_gen"]
