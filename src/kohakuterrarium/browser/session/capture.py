"""``screenshot`` and ``evaluate``: pixels and script results from the page."""

import json
from typing import Any

from kohakuterrarium.browser import results as fmt
from kohakuterrarium.browser.dialogs import DialogManager
from kohakuterrarium.browser.errors import (
    ActionFailedError,
    ActionSpecError,
    BrowserTimeoutError,
    DriverDisconnectedError,
    DriverError,
    PageClosedError,
    PageNavigatingError,
)
from kohakuterrarium.browser.images import image_size, mime_type
from kohakuterrarium.browser.observe import origin_of
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.session.types import (
    EVAL_MAX_CHARS,
    FULL_PAGE_MAX_HEIGHT,
    JPEG_QUALITIES,
    BrowserResult,
    Op,
)
from kohakuterrarium.browser.tabs import Tab, TabRegistry


class SessionCaptureMixin:
    """``screenshot`` and ``evaluate``; stateless beyond the parts it reads."""

    options: BrowserOptions
    # owned by SessionLifecycleMixin
    _tabs: TabRegistry
    _dialogs: DialogManager

    async def _ref_problem(
        self, tab: Tab, op: Op, ref: str | None, what: str
    ) -> BrowserResult | None:
        """The error result when ``ref`` is stale or unknown; ``None`` when it may be used."""
        if ref is None:
            return None
        try:
            await self._check_ref(tab, ref, await self._fresh_refs(tab))
        except (ActionFailedError, PageNavigatingError) as exc:
            return self._error_result(tab, op, f"{what}: {exc}", exc.detail)
        return None

    async def screenshot(
        self,
        *,
        ref: str | None = None,
        full_page: bool = False,
        max_bytes: int | None = None,
        max_height: int = FULL_PAGE_MAX_HEIGHT,
        tab_id: str | None = None,
    ) -> BrowserResult:
        """A CSS-pixel screenshot: viewport, one element, or a height-capped full page.

        PNG unless it exceeds ``max_bytes``; then JPEG at falling quality.
        """
        if ref is not None and full_page:
            raise ActionSpecError("give either ref or full_page, not both")
        async with self._operation() as op:
            if self._dialogs.pending is not None:
                return self._pending_result(op)
            tab = op.tab = self._tabs.get(tab_id)
            problem = await self._ref_problem(tab, op, ref, "Screenshot not taken")
            if problem is not None:
                return problem
            timeout = self.options.action_timeout_s
            attempts = [("png", None)] + [("jpeg", q) for q in JPEG_QUALITIES]
            data = b""
            for image_type, quality in attempts:
                data = await self._call(
                    tab,
                    lambda: tab.page.screenshot(
                        ref=ref,
                        full_page=full_page,
                        max_height=max_height,
                        image_type=image_type,
                        quality=quality,
                        timeout=timeout,
                    ),
                    timeout,
                    "taking the screenshot",
                )
                if max_bytes is None or len(data) <= max_bytes:
                    break
            width, height = image_size(data) or (0, 0)
            mime = mime_type(data)
            line = f"Screenshot: {width}x{height} CSS px, {mime}, {len(data)} bytes"
            if max_bytes is not None and len(data) > max_bytes:
                line += f" (over the {max_bytes}-byte budget even as JPEG)"
            text = fmt.join_sections(
                self._header(tab, tab.page.url), line, self._notes(op, None)
            )
            meta = self._meta(tab, None, width=width, height=height)
            return BrowserResult(self._redact(text, tab), meta, data, mime)

    async def evaluate(
        self, expression: str, *, ref: str | None = None, tab_id: str | None = None
    ) -> BrowserResult:
        """Run JavaScript in the page (with the ``ref`` element as argument); JSON result.

        A thrown error or a stale ``ref`` comes back as text with ``is_error``;
        a script that runs past the action timeout raises ``BrowserTimeoutError``.
        """
        if not isinstance(expression, str) or not expression.strip():
            raise ActionSpecError("expression must be non-empty JavaScript")
        async with self._operation() as op:
            if self._dialogs.pending is not None:
                return self._pending_result(op)
            tab = op.tab = self._tabs.get(tab_id)
            problem = await self._ref_problem(tab, op, ref, "Script not run")
            if problem is not None:
                return problem
            timeout = self.options.action_timeout_s
            failed = False
            try:
                value = await self._call(
                    tab,
                    lambda: tab.page.evaluate(expression, ref=ref, timeout=timeout),
                    timeout,
                    "the script",
                )
                body = json_text(value)
            except (BrowserTimeoutError, TimeoutError):
                raise BrowserTimeoutError(
                    f"the script did not finish within {timeout:g} s"
                ) from None
            except (DriverDisconnectedError, PageClosedError):
                raise
            except DriverError as exc:
                failed = True
                body = f"The script threw: {self._detail(exc.detail or str(exc), tab)}"
            body = self._redact(body, tab)
            if len(body) > EVAL_MAX_CHARS:
                cut = len(body) - EVAL_MAX_CHARS
                body = body[:EVAL_MAX_CHARS] + f"\n… {cut} more characters cut"
            url = tab.page.url
            text = fmt.join_sections(
                self._header(tab, url),
                self._wrap(body, tab, origin_of(url), url),
                self._notes(op, None),
            )
            meta = self._meta(tab, None)
            return BrowserResult(self._redact(text, tab), meta, is_error=failed)


def json_text(value: Any) -> str:
    """``value`` as compact JSON, or a plain note when it is not JSON-serialisable."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return (
            f"(the result is not JSON-serialisable: {type(value).__name__}) {value!r}"
        )
