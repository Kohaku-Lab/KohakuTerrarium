"""YAML round-trip tests — comments and key order must survive."""

from pathlib import Path

from kohakuterrarium.studio.editors.yaml_creature import (
    load_creature_file,
    save_creature_file,
    save_creature_merged,
)

SAMPLE_YAML = """\
name: swe
version: "1.0"
# Inherit from general
base_config: "@kt-biome/creatures/general"

controller:
  # Override reasoning for SWE tasks
  reasoning_effort: high

system_prompt_file: prompts/system.md
"""


def test_load_parses(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    data = load_creature_file(p)
    assert data["name"] == "swe"
    assert data["base_config"] == "@kt-biome/creatures/general"
    assert data["controller"]["reasoning_effort"] == "high"


def test_save_merged_preserves_top_level_comment(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")

    save_creature_merged(p, {"controller": {"reasoning_effort": "low"}})

    text = p.read_text(encoding="utf-8")
    # Comment we care about must still be there
    assert "# Inherit from general" in text
    assert "# Override reasoning for SWE tasks" in text
    # Value was updated
    assert "reasoning_effort: low" in text
    # Original top-level structure preserved
    assert "name: swe" in text


def test_save_merged_adds_new_key_without_losing_others(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")

    save_creature_merged(p, {"description": "swe dev"})

    text = p.read_text(encoding="utf-8")
    assert "description: swe dev" in text
    assert "name: swe" in text
    assert "base_config" in text


def test_save_merged_replaces_lists_wholesale(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "tools:\n  - {name: bash, type: builtin}\n  - {name: read, type: builtin}\n",
        encoding="utf-8",
    )
    save_creature_merged(p, {"tools": [{"name": "write", "type": "builtin"}]})
    data = load_creature_file(p)
    names = [t["name"] for t in data["tools"]]
    assert names == ["write"]


def test_save_creature_file_creates_fresh(tmp_path: Path):
    p = tmp_path / "config.yaml"
    save_creature_file(p, {"name": "x", "version": "1.0"})
    assert p.exists()
    data = load_creature_file(p)
    assert data["name"] == "x"


def test_merged_handles_missing_file(tmp_path: Path):
    p = tmp_path / "config.yaml"
    save_creature_merged(p, {"name": "x"})
    assert p.exists()
    assert load_creature_file(p)["name"] == "x"


def test_load_returns_empty_dict_on_empty_file(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    assert load_creature_file(p) == {}
