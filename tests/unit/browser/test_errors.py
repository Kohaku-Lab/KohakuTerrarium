"""Unit tests for ``kohakuterrarium.browser.errors``."""

import pytest

from kohakuterrarium.browser import errors as err
from kohakuterrarium.errors import ConflictError, KTError


@pytest.mark.parametrize(
    "cls",
    [
        err.BrowserError,
        err.BrowserTimeoutError,
        err.NavigationBlockedError,
        err.ActionFailedError,
        err.DialogPendingError,
        err.TabLimitError,
        err.DriverError,
        err.DriverTimeoutError,
    ],
)
def test_message_and_page_detail_are_separate(cls):
    exc = cls("the call failed", detail="page said </page_content>")
    assert isinstance(exc, KTError)
    assert str(exc) == "the call failed"
    assert exc.detail == "page said </page_content>"
    assert cls("plain").detail == ""


def test_with_text_rewrites_message_and_detail_in_place():
    exc = err.BrowserTimeoutError("loading x timed out", detail="secret here")
    same = exc.with_text(lambda text: text.replace("x", "y").replace("secret", "[s]"))
    assert same is exc
    assert str(exc) == "loading y timed out" and exc.detail == "[s] here"
    assert isinstance(exc, TimeoutError)


def test_dialog_opened_keeps_its_record():
    record = object()
    exc = err.DialogOpenedError(record, "a confirm dialog opened", detail="Sure?")
    assert exc.record is record
    assert str(exc) == "a confirm dialog opened" and exc.detail == "Sure?"


def test_tab_limit_is_a_conflict():
    assert isinstance(err.TabLimitError("full"), ConflictError)
