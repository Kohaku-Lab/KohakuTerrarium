"""Unit tests for ``scripts/set_pyproject_version.py`` — the version source of truth.

The nightly version used to be a literal in ``nightly.yml``. It drifted behind
``pyproject.toml`` and, being a dev release of the *current* version, sorted
below the release it was cut after — so ``pip install --pre`` never saw it as
an upgrade. It also carried a ``+local`` segment, which PyPI rejects outright.
"""

import importlib.util
from pathlib import Path

from datetime import datetime, timezone

import pytest
from packaging.version import Version

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "set_pyproject_version.py"

spec = importlib.util.spec_from_file_location("set_pyproject_version", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestNightlyVersion:
    @pytest.mark.parametrize(
        "current,expected_core",
        [("2.1.1", "2.1.2"), ("2.0.0", "2.0.1"), ("10.4.9", "10.4.10")],
    )
    def test_bumps_the_smallest_digit(self, current, expected_core):
        assert mod.nightly(current).startswith(f"{expected_core}.dev")

    def test_sorts_above_the_release_it_follows(
        self,
    ):
        # The whole point: a nightly cut after 2.1.1 must be an upgrade from it.
        assert Version(mod.nightly("2.1.1")) > Version("2.1.1")

    def test_sorts_below_the_release_it_anticipates(self):
        assert Version(mod.nightly("2.1.1")) < Version("2.1.2")

    def test_carries_no_local_segment(self):
        # PyPI rejects local versions; that was the only thing keeping
        # nightlies off PyPI.
        assert "+" not in mod.nightly("2.1.1")

    def test_stamp_is_utc_and_monotonic(self):
        a, b = mod.nightly("2.1.1"), mod.nightly("2.1.1")
        assert Version(a) <= Version(b)

    def test_stamp_is_the_utc_date_only(self):
        # Short and PyPI-friendly, and identical to the nightly-YYYYmmdd
        # release tag so a version names the tag it shipped under.
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        assert mod.nightly("2.1.1") == f"2.1.2.dev{today}"

    def test_rejects_an_unparseable_version(self):
        with pytest.raises(SystemExit):
            mod.nightly("not-a-version")


class TestReadAndWrite:
    def test_read_returns_the_project_version(self):
        assert Version(mod.read())

    def test_write_round_trips(self, tmp_path, monkeypatch):
        fake = tmp_path / "pyproject.toml"
        fake.write_text(
            '[project]\nversion = "1.2.3"\n\n[tool.briefcase]\nversion = "1.2.3"\n'
        )
        monkeypatch.setattr(mod, "PYPROJECT", fake)
        assert mod.read() == "1.2.3"
        mod.patch("9.9.9.dev1")
        # Both the project and briefcase fields move; briefcase reads its own.
        assert fake.read_text().count('version = "9.9.9.dev1"') == 2
        assert mod.read() == "9.9.9.dev1"
