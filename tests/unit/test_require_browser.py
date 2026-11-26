"""``KT_TEST_REQUIRE_BROWSER=1`` turns a skip for want of Playwright or Chromium into a failure."""

import pytest

from tests.helpers.browser_site import REQUIRE_BROWSER_ENV, enforce_browser

NO_CHROMIUM = "no Chromium for Playwright: Executable doesn't exist at /x/chrome"
NO_PLAYWRIGHT = "could not import 'playwright.async_api': No module named 'playwright'"


def skipped(reason: str, when: str = "call") -> pytest.TestReport:
    """The report pytest makes for a test or fixture that called ``pytest.skip``."""
    return pytest.TestReport(
        "t.py::test", ("t.py", 0, "test"), {}, "skipped", ("t.py", 3, reason), when
    )


@pytest.mark.parametrize("reason", [NO_CHROMIUM, NO_PLAYWRIGHT])
@pytest.mark.parametrize("when", ["setup", "call"])
def test_a_browser_skip_fails_when_the_browser_is_required(reason, when):
    report = skipped(f"Skipped: {reason}", when)
    enforce_browser(report, {REQUIRE_BROWSER_ENV: "1"})
    assert report.failed and not report.skipped
    assert str(report.longrepr) == (
        f"{REQUIRE_BROWSER_ENV}=1 but the test skipped: Skipped: {reason}"
    )


@pytest.mark.parametrize(
    "environ", [{}, {REQUIRE_BROWSER_ENV: ""}, {REQUIRE_BROWSER_ENV: "0"}]
)
def test_a_browser_skip_stays_a_skip_unless_required(environ):
    report = skipped(f"Skipped: {NO_CHROMIUM}")
    enforce_browser(report, environ)
    assert report.skipped
    assert report.longrepr == ("t.py", 3, f"Skipped: {NO_CHROMIUM}")


def test_other_skips_and_outcomes_are_left_alone():
    required = {REQUIRE_BROWSER_ENV: "1"}
    other = skipped("Skipped: Windows-only loop class")
    enforce_browser(other, required)
    assert other.skipped
    passed = pytest.TestReport(
        "t.py::test", ("t.py", 0, "test"), {}, "passed", None, "call"
    )
    enforce_browser(passed, required)
    assert passed.passed
    failed = pytest.TestReport(
        "t.py::test", ("t.py", 0, "test"), {}, "failed", "boom", "call"
    )
    enforce_browser(failed, required)
    assert failed.failed and failed.longrepr == "boom"


def test_the_suite_reports_a_browser_skip_as_a_failure_when_required(
    request, monkeypatch
):
    monkeypatch.setenv(REQUIRE_BROWSER_ENV, "1")
    call = pytest.CallInfo.from_call(lambda: pytest.skip(NO_CHROMIUM), when="call")
    report = request.config.hook.pytest_runtest_makereport(item=request.node, call=call)
    assert report.failed
    assert NO_CHROMIUM in str(report.longrepr)
    monkeypatch.delenv(REQUIRE_BROWSER_ENV)
    report = request.config.hook.pytest_runtest_makereport(item=request.node, call=call)
    assert report.skipped
