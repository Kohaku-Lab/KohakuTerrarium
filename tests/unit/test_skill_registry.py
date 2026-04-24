"""Unit tests for the procedural-skill registry + discovery layer."""

import json
from pathlib import Path

import pytest

from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.skills import (
    SCRATCHPAD_ENABLED_KEY,
    Skill,
    SkillRegistry,
    discover_skills,
    load_skill_from_path,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_skill_folder(root: Path, name: str, body: str = "Hello", **fm) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    frontmatter_lines = ["---", f"name: {name}", f"description: {name}-desc"]
    for key, value in fm.items():
        if isinstance(value, list):
            quoted = ", ".join(f'"{v}"' for v in value)
            frontmatter_lines.append(f"{key}: [{quoted}]")
        elif isinstance(value, bool):
            frontmatter_lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            frontmatter_lines.append(f"{key}: {value}")
    frontmatter_lines.append("---")
    text = "\n".join(frontmatter_lines) + "\n\n" + body + "\n"
    skill_md = folder / "SKILL.md"
    skill_md.write_text(text, encoding="utf-8")
    return skill_md


def _write_flat_skill(root: Path, name: str, body: str = "Hi") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    md = root / f"{name}.md"
    md.write_text(
        f"---\nname: {name}\ndescription: flat-{name}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return md


# ---------------------------------------------------------------------------
# load_skill_from_path
# ---------------------------------------------------------------------------


def test_load_skill_folder_form(tmp_path):
    skill_md = _write_skill_folder(tmp_path, "foo", body="body text")
    skill = load_skill_from_path(skill_md, origin="user")
    assert skill is not None
    assert skill.name == "foo"
    assert skill.description == "foo-desc"
    assert skill.body.strip() == "body text"
    assert skill.origin == "user"


def test_load_skill_flat_form(tmp_path):
    md = _write_flat_skill(tmp_path, "bar", body="flat body")
    skill = load_skill_from_path(md, origin="project")
    assert skill is not None
    assert skill.name == "bar"
    assert skill.description == "flat-bar"
    assert skill.origin == "project"


def test_load_skill_missing_returns_none(tmp_path):
    assert load_skill_from_path(tmp_path / "missing.md", origin="user") is None


def test_load_skill_parses_paths_and_allowed_tools(tmp_path):
    skill_md = _write_skill_folder(
        tmp_path,
        "pdf-merge",
        paths=["*.pdf", "docs/**"],
        **{"allowed-tools": ["read", "bash"]},
    )
    skill = load_skill_from_path(skill_md, origin="user")
    assert skill is not None
    assert skill.paths == ["*.pdf", "docs/**"]
    assert skill.allowed_tools == ["read", "bash"]


def test_load_skill_disable_model_invocation(tmp_path):
    skill_md = _write_skill_folder(
        tmp_path, "hidden", **{"disable-model-invocation": True}
    )
    skill = load_skill_from_path(skill_md, origin="user")
    assert skill is not None
    assert skill.disable_model_invocation is True
    assert skill.invocation_blocked is True


# ---------------------------------------------------------------------------
# discover_skills — priority & last-wins
# ---------------------------------------------------------------------------


def test_discover_scans_project_and_user_in_priority_order(tmp_path):
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    _write_skill_folder(cwd / ".kt" / "skills", "foo", body="PROJECT")
    _write_skill_folder(home / ".kohakuterrarium" / "skills", "foo", body="USER")

    skills = discover_skills(cwd=cwd, home=home)
    # Package layer skipped when no packages installed; creature skipped
    # when no agent_path.  Project should be registered LAST so it
    # overrides the user entry under last-wins semantics.
    names = [s.name for s in skills]
    assert names.count("foo") == 2
    # Registration order: user first, project last.
    assert skills[-1].body.strip() == "PROJECT"


def test_discover_folder_beats_flat_form(tmp_path):
    cwd = tmp_path / "project"
    _write_skill_folder(cwd / ".kt" / "skills", "dup", body="FOLDER")
    _write_flat_skill(cwd / ".kt" / "skills", "dup", body="FLAT")

    skills = discover_skills(cwd=cwd, home=tmp_path / "home")
    dups = [s for s in skills if s.name == "dup"]
    # Folder form shadows flat form within the same root — only one entry.
    assert len(dups) == 1
    assert dups[0].body.strip() == "FOLDER"


def test_discover_creature_scope(tmp_path):
    agent = tmp_path / "my-agent"
    _write_skill_folder(agent / "prompts" / "skills", "creature-skill")
    skills = discover_skills(
        cwd=tmp_path / "cwd", home=tmp_path / "home", agent_path=agent
    )
    creature_skills = [s for s in skills if s.origin == "creature"]
    assert len(creature_skills) == 1
    assert creature_skills[0].name == "creature-skill"


# ---------------------------------------------------------------------------
# SkillRegistry semantics
# ---------------------------------------------------------------------------


def _make_skill(name: str, **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.pop("description", name),
        body=kw.pop("body", "body"),
        origin=kw.pop("origin", "user"),
        **kw,
    )


def test_registry_add_overrides_last_wins():
    reg = SkillRegistry()
    reg.add(_make_skill("x", body="first", origin="package"))
    reg.add(_make_skill("x", body="second", origin="project"))
    assert reg.get("x").body == "second"
    assert reg.get("x").origin == "project"


def test_registry_enable_disable_persists_to_scratchpad():
    sp = Scratchpad()
    reg = SkillRegistry(scratchpad=sp)
    reg.add(_make_skill("x", enabled=True))
    reg.disable("x")
    raw = sp.get(SCRATCHPAD_ENABLED_KEY)
    assert raw is not None
    assert json.loads(raw) == {"x": False}
    reg.enable("x")
    assert json.loads(sp.get(SCRATCHPAD_ENABLED_KEY)) == {"x": True}


def test_registry_hydrates_from_existing_scratchpad_state():
    sp = Scratchpad()
    sp.set(SCRATCHPAD_ENABLED_KEY, json.dumps({"x": False}))
    reg = SkillRegistry(scratchpad=sp)
    reg.add(_make_skill("x", enabled=True))
    assert reg.get("x").enabled is False


def test_registry_corrupt_scratchpad_does_not_raise():
    sp = Scratchpad()
    sp.set(SCRATCHPAD_ENABLED_KEY, "not-valid-json")
    reg = SkillRegistry(scratchpad=sp)
    reg.add(_make_skill("x"))
    assert reg.get("x").enabled is True


def test_registry_enable_returns_false_for_unknown():
    reg = SkillRegistry()
    assert reg.enable("missing") is False
    assert reg.disable("missing") is False


def test_registry_list_enabled_filters_disabled():
    reg = SkillRegistry()
    reg.add(_make_skill("a", enabled=True))
    reg.add(_make_skill("b", enabled=False))
    names = [s.name for s in reg.list_enabled()]
    assert names == ["a"]


def test_registry_names_sorted():
    reg = SkillRegistry()
    reg.add(_make_skill("c"))
    reg.add(_make_skill("a"))
    reg.add(_make_skill("b"))
    assert reg.names() == ["a", "b", "c"]


def test_registry_contains_and_len():
    reg = SkillRegistry()
    assert len(reg) == 0
    assert "x" not in reg
    reg.add(_make_skill("x"))
    assert "x" in reg
    assert len(reg) == 1


# ---------------------------------------------------------------------------
# paths matching
# ---------------------------------------------------------------------------


def test_path_matching_basename(tmp_path):
    from kohakuterrarium.skills.paths import SkillPathScanner

    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "doc.pdf").write_text("x", encoding="utf-8")

    reg = SkillRegistry()
    reg.add(_make_skill("pdf", paths=["*.pdf"]))
    matched = SkillPathScanner().matching_skills(reg, tmp_path)
    assert [s.name for s in matched] == ["pdf"]


def test_path_matching_no_match(tmp_path):
    from kohakuterrarium.skills.paths import SkillPathScanner

    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_make_skill("pdf", paths=["*.pdf"]))
    matched = SkillPathScanner().matching_skills(reg, tmp_path)
    assert matched == []


def test_path_matching_respects_enabled(tmp_path):
    from kohakuterrarium.skills.paths import SkillPathScanner

    (tmp_path / "x.pdf").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_make_skill("pdf", paths=["*.pdf"], enabled=False))
    matched = SkillPathScanner().matching_skills(reg, tmp_path)
    assert matched == []


def test_path_matching_ignores_skills_without_paths(tmp_path):
    from kohakuterrarium.skills.paths import SkillPathScanner

    (tmp_path / "x.pdf").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_make_skill("pdf"))  # no paths
    matched = SkillPathScanner().matching_skills(reg, tmp_path)
    assert matched == []


def test_path_hint_skips_disable_model_invocation():
    from kohakuterrarium.skills.paths import SkillPathScanner

    scanner = SkillPathScanner()
    hidden = _make_skill("hidden", paths=["*.x"], disable_model_invocation=True)
    assert scanner.format_hint([hidden]) == ""


def test_path_cache_reuses_file_list(tmp_path):
    """Repeat scans in the same mtime window reuse the cached file list."""
    from kohakuterrarium.skills.paths import SkillPathScanner

    (tmp_path / "a.pdf").write_text("x", encoding="utf-8")
    scanner = SkillPathScanner()
    reg = SkillRegistry()
    reg.add(_make_skill("pdf", paths=["*.pdf"]))
    first = scanner.matching_skills(reg, tmp_path)
    second = scanner.matching_skills(reg, tmp_path)
    assert first == second


@pytest.mark.parametrize(
    "pattern, filename, matches",
    [
        ("*.pdf", "report.pdf", True),
        ("src/**/*.py", "src/foo.py", True),
        ("*.pdf", "report.txt", False),
        ("docs/*.md", "docs/readme.md", True),
    ],
)
def test_pattern_matching_variations(tmp_path, pattern, filename, matches):
    from kohakuterrarium.skills.paths import SkillPathScanner

    parent = tmp_path / Path(filename).parent
    parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / filename).write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_make_skill("skill", paths=[pattern]))
    found = SkillPathScanner().matching_skills(reg, tmp_path)
    assert bool(found) is matches


# ---------------------------------------------------------------------------
# Package-skill enable-by-default / wildcard opt-in
# ---------------------------------------------------------------------------


def _stub_one_package_skill(
    monkeypatch,
    tmp_path: Path,
    *,
    pkg_name: str,
    skill_names: list[str],
) -> None:
    """Lay out ``tmp_path/<pkg_name>/skills/<name>/SKILL.md`` files and
    monkeypatch the discovery layer to surface them as one installed
    package."""
    from kohakuterrarium.skills import discovery as disc

    pkg_root = tmp_path / pkg_name
    for name in skill_names:
        _write_skill_folder(pkg_root / "skills", name, body=f"PKG {name}")

    entries = [
        (pkg_name, {"name": n, "path": f"skills/{n}", "_root": str(pkg_root)})
        for n in skill_names
    ]
    monkeypatch.setattr(disc, "list_package_skills_with_owner", lambda: entries)
    monkeypatch.setattr(
        disc,
        "get_package_path",
        lambda name: pkg_root if name == pkg_name else None,
    )


def test_package_skill_defaults_disabled_without_declaration(monkeypatch, tmp_path):
    """Without a ``skills:`` entry, package-provided skills load disabled."""
    _stub_one_package_skill(
        monkeypatch, tmp_path, pkg_name="demo-pack", skill_names=["foo"]
    )

    skills = discover_skills(cwd=tmp_path / "proj", home=tmp_path / "home")
    foo = next(s for s in skills if s.name == "foo")
    assert foo.origin.startswith("package")
    assert foo.enabled is False


def test_package_skill_enabled_when_named_in_declared(monkeypatch, tmp_path):
    """``declared_package_skills=['foo']`` flips that one skill on."""
    _stub_one_package_skill(
        monkeypatch, tmp_path, pkg_name="demo-pack", skill_names=["foo", "bar"]
    )

    skills = discover_skills(
        cwd=tmp_path / "proj",
        home=tmp_path / "home",
        declared_package_skills=["foo"],
    )
    foo = next(s for s in skills if s.name == "foo")
    bar = next(s for s in skills if s.name == "bar")
    assert foo.enabled is True
    assert bar.enabled is False


def test_wildcard_enables_every_package_skill(monkeypatch, tmp_path):
    """``declared_package_skills=['*']`` enables every discovered package skill."""
    _stub_one_package_skill(
        monkeypatch,
        tmp_path,
        pkg_name="demo-pack",
        skill_names=["foo", "bar", "baz"],
    )

    skills = discover_skills(
        cwd=tmp_path / "proj",
        home=tmp_path / "home",
        declared_package_skills=["*"],
    )
    pkg_skills = [s for s in skills if s.origin.startswith("package")]
    assert len(pkg_skills) == 3
    assert all(s.enabled for s in pkg_skills)


def test_wildcard_combines_with_named_entries(monkeypatch, tmp_path):
    """Mixed ``['*', 'foo']`` is equivalent to wildcard alone — all enabled."""
    _stub_one_package_skill(
        monkeypatch, tmp_path, pkg_name="demo-pack", skill_names=["foo", "bar"]
    )

    skills = discover_skills(
        cwd=tmp_path / "proj",
        home=tmp_path / "home",
        declared_package_skills=["*", "foo"],
    )
    pkg_skills = [s for s in skills if s.origin.startswith("package")]
    assert all(s.enabled for s in pkg_skills)


def test_wildcard_not_treated_as_literal_skill_name(monkeypatch, tmp_path):
    """The ``*`` entry is consumed as the wildcard, never registered."""
    _stub_one_package_skill(
        monkeypatch, tmp_path, pkg_name="demo-pack", skill_names=["foo"]
    )

    skills = discover_skills(
        cwd=tmp_path / "proj",
        home=tmp_path / "home",
        declared_package_skills=["*"],
    )
    pkg_names = [s.name for s in skills if s.origin.startswith("package")]
    assert pkg_names == ["foo"]
