"""Composition algebra core — protocol, operators, and combinators.

All operators live in ONE file to avoid circular imports.
``BaseRunnable`` defines the operator overloads, and all combinators
(Sequence, Product, Fallback, …) are defined after it in the same module.

Operators:
  ``>>``  sequence (auto-wraps plain callables)
  ``&``   parallel product (siblings cancelled on first failure)
  ``|``   fallback (try first, if exception try second; failures chain)
  ``*N``  retry N times
  ``()``  run (await pipeline(x))
  ``.retry(n, backoff=…)``  retry with exponential backoff
  ``.iterate(x)``  async-for loop
  ``.map(fn)``  / ``.contramap(fn)``  profunctor transforms
  ``.fails_when(pred)``  custom failure predicate
"""

import asyncio
import inspect
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# ── Protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class Runnable(Protocol):
    """Anything that takes input and produces output asynchronously."""

    async def run(self, input: Any) -> Any: ...


# ── Sentinel for iterate .feed() ─────────────────────────────────────

_SENTINEL = object()


# ── BaseRunnable ─────────────────────────────────────────────────────


class BaseRunnable:
    """Concrete base providing Pythonic operator overloads.

    Combinators inherit this so they all get ``>>``, ``&``, ``|``, ``*``.
    """

    async def run(self, input: Any) -> Any:
        raise NotImplementedError

    async def __call__(self, input: Any) -> Any:
        """``await pipeline(x)`` is sugar for ``await pipeline.run(x)``."""
        return await self.run(input)

    # ── Sequence (>>) ────────────────────────────────────────────────

    def __rshift__(self, other: Any) -> "BaseRunnable":
        if isinstance(other, dict):
            routes = {
                k: v if isinstance(v, BaseRunnable) else Pure(v)
                for k, v in other.items()
                if callable(v) or isinstance(v, BaseRunnable)
            }
            return Sequence._flat(self, Router(routes))
        if isinstance(other, BaseRunnable):
            return Sequence._flat(self, other)
        if callable(other):
            return Sequence._flat(self, Pure(other))
        return NotImplemented

    def __rrshift__(self, other: Any) -> "BaseRunnable":
        if callable(other) and not isinstance(other, BaseRunnable):
            return Sequence._flat(Pure(other), self)
        return NotImplemented

    # ── Parallel (&) ─────────────────────────────────────────────────

    def __and__(self, other: Any) -> "BaseRunnable":
        if isinstance(other, BaseRunnable):
            return Product._flat(self, other)
        if callable(other):
            return Product._flat(self, Pure(other))
        return NotImplemented

    # ── Fallback (|) ─────────────────────────────────────────────────

    def __or__(self, other: Any) -> "BaseRunnable":
        if isinstance(other, BaseRunnable):
            return Fallback(self, other)
        if callable(other):
            return Fallback(self, Pure(other))
        return NotImplemented

    # ── Retry (* N) ──────────────────────────────────────────────────

    def __mul__(self, n: Any) -> "BaseRunnable":
        if isinstance(n, int) and n > 0:
            return Retry(self, n)
        return NotImplemented

    def __rmul__(self, n: Any) -> "BaseRunnable":
        return self.__mul__(n)

    def retry(
        self, max_attempts: int, *, backoff: float = 0.0, max_backoff: float = 30.0
    ) -> "BaseRunnable":
        """Retry with optional exponential backoff.

        ``pipeline * 3`` is sugar for ``pipeline.retry(3)``; use this
        form when you need a delay between attempts: ``backoff`` is the
        sleep after the first failure, doubling per attempt and capped
        at ``max_backoff`` seconds.
        """
        return Retry(self, max_attempts, backoff=backoff, max_backoff=max_backoff)

    # ── Iterate (async for) ──────────────────────────────────────────

    def iterate(self, initial_input: Any) -> "PipelineIterator":
        """Return an async iterator that feeds output back as input.

        Usage::

            async for result in pipeline.iterate("start"):
                if done(result):
                    break
        """
        return PipelineIterator(self, initial_input)

    # ── Profunctor maps ──────────────────────────────────────────────

    def map(self, fn: Callable) -> "BaseRunnable":
        """Post-process output: ``self >> pure(fn)``."""
        return Sequence._flat(self, Pure(fn))

    def contramap(self, fn: Callable) -> "BaseRunnable":
        """Pre-process input: ``pure(fn) >> self``."""
        return Sequence._flat(Pure(fn), self)

    # ── Failure predicate ────────────────────────────────────────────

    def fails_when(self, predicate: Callable[[Any], bool]) -> "BaseRunnable":
        """Wrap so that output matching *predicate* raises (triggers fallback)."""
        return FailsWhen(self, predicate)

    # ── repr ─────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"


# ── Pure ─────────────────────────────────────────────────────────────


class Pure(BaseRunnable):
    """Wrap a sync or async callable as a zero-cost Runnable."""

    def __init__(self, fn: Any):
        if not callable(fn):
            raise TypeError(f"Pure requires a callable, got {type(fn)}")
        self._fn = fn

    async def run(self, input: Any) -> Any:
        result = self._fn(input)
        if inspect.isawaitable(result):
            return await result
        return result

    def __repr__(self) -> str:
        name = getattr(self._fn, "__name__", repr(self._fn))
        return f"<Pure {name}>"


# Lowercase alias — the documented spelling for wrapping a function
# inline: ``pure(extract_code) >> reviewer``.
pure = Pure


# ── Sequence ─────────────────────────────────────────────────────────


class Sequence(BaseRunnable):
    """Run steps in order, piping each output as the next input."""

    def __init__(self, *steps: BaseRunnable):
        self._steps: tuple[BaseRunnable, ...] = steps

    async def run(self, input: Any) -> Any:
        result = input
        for step in self._steps:
            result = await step.run(result)
        return result

    @classmethod
    def _flat(cls, *parts: BaseRunnable) -> "Sequence":
        """Build a flattened Sequence (merge nested Sequences)."""
        flat: list[BaseRunnable] = []
        for p in parts:
            if isinstance(p, Sequence):
                flat.extend(p._steps)
            else:
                flat.append(p)
        return cls(*flat)

    def __repr__(self) -> str:
        inner = " >> ".join(repr(s) for s in self._steps)
        return f"<Sequence {inner}>"


# ── Product (Parallel) ───────────────────────────────────────────────


class Product(BaseRunnable):
    """Run branches concurrently, return tuple of results.

    On the first branch failure the surviving siblings are CANCELLED
    and awaited before the exception propagates — a bare ``gather``
    would leave them running detached, burning LLM turns whose
    results nobody will ever read.
    """

    def __init__(self, *branches: BaseRunnable):
        self._branches: tuple[BaseRunnable, ...] = branches

    async def run(self, input: Any) -> tuple[Any, ...]:
        tasks = [asyncio.ensure_future(branch.run(input)) for branch in self._branches]
        try:
            return tuple(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    @classmethod
    def _flat(cls, *parts: BaseRunnable) -> "Product":
        """Build a flattened Product (merge nested Products)."""
        flat: list[BaseRunnable] = []
        for p in parts:
            if isinstance(p, Product):
                flat.extend(p._branches)
            else:
                flat.append(p)
        return cls(*flat)

    def __repr__(self) -> str:
        inner = " & ".join(repr(b) for b in self._branches)
        return f"<Product {inner}>"


# ── Fallback ─────────────────────────────────────────────────────────


class Fallback(BaseRunnable):
    """Try primary; if it raises ``Exception``, run fallback instead.

    When the fallback ALSO fails, the primary's exception is chained
    as the ``__cause__`` — debugging a dead pipeline needs the
    original failure, not just the last resort's.
    """

    def __init__(self, primary: BaseRunnable, fallback: BaseRunnable):
        self._primary = primary
        self._fallback = fallback

    async def run(self, input: Any) -> Any:
        try:
            return await self._primary.run(input)
        except Exception as primary_exc:
            logger.debug(
                "Fallback triggered",
                primary=repr(self._primary),
                fallback=repr(self._fallback),
                error=str(primary_exc),
            )
            try:
                return await self._fallback.run(input)
            except Exception as fallback_exc:
                raise fallback_exc from primary_exc

    def __repr__(self) -> str:
        return f"<Fallback {self._primary!r} | {self._fallback!r}>"


# ── FailsWhen ────────────────────────────────────────────────────────


class FailsWhen(BaseRunnable):
    """Wrap a Runnable — raise ``ValueError`` when predicate matches output."""

    def __init__(self, inner: BaseRunnable, predicate: Callable[[Any], bool]):
        self._inner = inner
        self._predicate = predicate

    async def run(self, input: Any) -> Any:
        result = await self._inner.run(input)
        if self._predicate(result):
            raise ValueError(f"FailsWhen predicate triggered on: {str(result)[:100]}")
        return result

    def __repr__(self) -> str:
        return f"<FailsWhen {self._inner!r}>"


# ── Retry ────────────────────────────────────────────────────────────


class Retry(BaseRunnable):
    """Retry a Runnable up to *max_attempts* times on ``Exception``.

    ``backoff`` (seconds) sleeps after each failed attempt, doubling
    every attempt and capped at ``max_backoff``. The default 0.0
    preserves ``pipeline * N`` semantics (immediate retry).
    """

    def __init__(
        self,
        inner: BaseRunnable,
        max_attempts: int,
        *,
        backoff: float = 0.0,
        max_backoff: float = 30.0,
    ):
        self._inner = inner
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._max_backoff = max_backoff

    async def run(self, input: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._inner.run(input)
            except Exception as e:
                last_error = e
                logger.debug(
                    "Retry attempt failed",
                    attempt=attempt,
                    max=self._max_attempts,
                    error=str(e)[:200],
                )
                if self._backoff > 0 and attempt < self._max_attempts:
                    delay = min(self._backoff * (2 ** (attempt - 1)), self._max_backoff)
                    await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]

    def __repr__(self) -> str:
        return f"<Retry {self._inner!r} * {self._max_attempts}>"


# ── Router ───────────────────────────────────────────────────────────


class Router(BaseRunnable):
    """Route to a branch by key.  Use ``_default`` for catch-all."""

    def __init__(self, routes: dict[str, BaseRunnable]):
        self._routes = dict(routes)
        self._default = self._routes.pop("_default", None)

    async def run(self, input: Any) -> Any:
        if isinstance(input, tuple) and len(input) == 2:
            key, payload = input
        else:
            key = input
            payload = input

        branch = self._routes.get(str(key))
        if branch is None:
            if self._default is not None:
                branch = self._default
            else:
                available = ", ".join(sorted(self._routes))
                raise KeyError(f"No route for key {key!r}. Available: {available}")
        return await branch.run(payload)

    def __repr__(self) -> str:
        keys = list(self._routes)
        if self._default:
            keys.append("_default")
        return f"<Router [{', '.join(keys)}]>"


# ── PipelineIterator ─────────────────────────────────────────────────


class PipelineIterator:
    """Async iterator that repeatedly runs a pipeline, feeding output back."""

    def __init__(self, pipeline: BaseRunnable, initial_input: Any):
        self._pipeline = pipeline
        self._next_input = initial_input
        self._override: Any = _SENTINEL

    def feed(self, value: Any) -> None:
        """Override the next iteration's input (instead of previous output)."""
        self._override = value

    def __aiter__(self) -> "PipelineIterator":
        return self

    async def __anext__(self) -> Any:
        if self._override is not _SENTINEL:
            inp = self._override
            self._override = _SENTINEL
        else:
            inp = self._next_input
        result = await self._pipeline.run(inp)
        self._next_input = result
        return result
