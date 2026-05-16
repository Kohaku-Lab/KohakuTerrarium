"""Unit tests for :mod:`kohakuterrarium.compose.core`."""

import pytest

from kohakuterrarium.compose.core import (
    BaseRunnable,
    FailsWhen,
    Fallback,
    Product,
    Pure,
    Retry,
    Router,
    Runnable,
    Sequence,
    _SENTINEL,
)

# ── Pure ─────────────────────────────────────────────────────────


class TestPure:
    async def test_sync_callable(self):
        p = Pure(lambda x: x + 1)
        assert await p.run(1) == 2

    async def test_async_callable(self):
        async def inc(x):
            return x + 1

        p = Pure(inc)
        assert await p.run(1) == 2

    def test_non_callable_raises(self):
        with pytest.raises(TypeError, match="callable"):
            Pure(42)

    def test_repr_with_named_fn(self):
        def my_fn(x):
            return x

        assert "my_fn" in repr(Pure(my_fn))

    def test_repr_with_lambda(self):
        # Lambdas have ``__name__`` of '<lambda>'.
        r = repr(Pure(lambda x: x))
        assert "Pure" in r


# ── BaseRunnable invocation ──────────────────────────────────────


class TestBaseRunnableCall:
    async def test_callable_sugar(self):
        p = Pure(lambda x: x * 2)
        assert await p(3) == 6

    async def test_run_not_implemented(self):
        b = BaseRunnable()
        with pytest.raises(NotImplementedError):
            await b.run(1)

    def test_repr(self):
        b = BaseRunnable()
        assert repr(b) == "<BaseRunnable>"


# ── Sequence ─────────────────────────────────────────────────────


class TestSequence:
    async def test_pipes_outputs(self):
        s = Sequence(Pure(lambda x: x + 1), Pure(lambda x: x * 2))
        assert await s.run(3) == 8  # (3+1)*2

    async def test_empty_sequence_returns_input(self):
        s = Sequence()
        assert await s.run(42) == 42

    def test_repr(self):
        s = Sequence(Pure(lambda x: x), Pure(lambda x: x))
        assert "Sequence" in repr(s)

    def test_flat_merges_nested(self):
        inner = Sequence(Pure(lambda x: x))
        outer = Sequence._flat(inner, Pure(lambda y: y))
        # All steps flattened into one tuple.
        assert len(outer._steps) == 2


class TestRshift:
    async def test_sequence_via_rshift(self):
        p1 = Pure(lambda x: x + 1)
        p2 = Pure(lambda x: x * 2)
        pipeline = p1 >> p2
        assert await pipeline.run(1) == 4

    async def test_rshift_with_callable(self):
        # Plain callable on the right is wrapped in Pure.
        p1 = Pure(lambda x: x + 1)
        pipeline = p1 >> (lambda x: x * 10)
        assert await pipeline.run(0) == 10

    def test_rshift_with_invalid_returns_notimplemented(self):
        p = Pure(lambda x: x)
        result = p.__rshift__(42)  # int is not callable
        assert result is NotImplemented

    async def test_rshift_with_dict_routes(self):
        # ``>> {"key": branch}`` builds a Router.
        p = Pure(lambda x: "a")
        pipeline = p >> {"a": Pure(lambda x: f"a-route-{x}")}
        out = await pipeline.run("hi")
        assert "a-route-" in out

    async def test_rrshift_callable_on_left(self):
        # plain_callable >> Runnable.
        p = Pure(lambda x: x * 2)

        def inc(x):
            return x + 1

        pipeline = inc >> p
        assert await pipeline.run(1) == 4

    def test_rrshift_with_runnable_returns_notimplemented(self):
        # When BOTH sides are Runnable, normal __rshift__ wins.
        p1 = Pure(lambda x: x)
        # __rrshift__ would be called with a non-runnable, non-callable.
        out = p1.__rrshift__(42)
        assert out is NotImplemented


# ── Product (parallel) ──────────────────────────────────────────


class TestProduct:
    async def test_parallel_run(self):
        p = Product(Pure(lambda x: x + 1), Pure(lambda x: x * 2))
        out = await p.run(3)
        assert out == (4, 6)

    def test_flat_merges_nested(self):
        inner = Product(Pure(lambda x: x))
        outer = Product._flat(inner, Pure(lambda y: y))
        assert len(outer._branches) == 2

    def test_repr(self):
        p = Product(Pure(lambda x: x), Pure(lambda x: x))
        assert "Product" in repr(p)


class TestAnd:
    async def test_parallel_via_and(self):
        p = Pure(lambda x: x + 1)
        pipeline = p & Pure(lambda x: x * 2)
        out = await pipeline.run(3)
        assert out == (4, 6)

    async def test_and_with_callable_on_right(self):
        p = Pure(lambda x: x + 1)
        pipeline = p & (lambda x: x * 10)
        out = await pipeline.run(2)
        assert out == (3, 20)

    def test_and_with_invalid_returns_notimplemented(self):
        p = Pure(lambda x: x)
        assert p.__and__(42) is NotImplemented


# ── Fallback ────────────────────────────────────────────────────


class TestFallback:
    async def test_primary_succeeds(self):
        f = Fallback(Pure(lambda x: x + 1), Pure(lambda x: x - 1))
        assert await f.run(3) == 4

    async def test_falls_back_on_exception(self):
        def boom(_x):
            raise RuntimeError("primary failed")

        f = Fallback(Pure(boom), Pure(lambda x: "fallback"))
        assert await f.run(0) == "fallback"

    def test_repr(self):
        f = Fallback(Pure(lambda x: x), Pure(lambda x: x))
        assert "Fallback" in repr(f)


class TestOr:
    async def test_fallback_via_or(self):
        def boom(_x):
            raise RuntimeError("nope")

        pipeline = Pure(boom) | Pure(lambda x: "ok")
        assert await pipeline.run(0) == "ok"

    async def test_or_with_callable_on_right(self):
        def boom(_x):
            raise RuntimeError("nope")

        pipeline = Pure(boom) | (lambda x: "callback")
        assert await pipeline.run(0) == "callback"

    def test_or_with_invalid_returns_notimplemented(self):
        p = Pure(lambda x: x)
        assert p.__or__(42) is NotImplemented


# ── Retry ───────────────────────────────────────────────────────


class TestRetry:
    async def test_succeeds_first_try(self):
        r = Retry(Pure(lambda x: x + 1), 3)
        assert await r.run(1) == 2

    async def test_retries_on_failure(self):
        attempts = []

        def flaky(_x):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("flaky")
            return "ok"

        r = Retry(Pure(flaky), 5)
        assert await r.run(0) == "ok"
        assert len(attempts) == 3

    async def test_exhausts_attempts_raises(self):
        def always_fail(_x):
            raise RuntimeError("always")

        r = Retry(Pure(always_fail), 2)
        with pytest.raises(RuntimeError, match="always"):
            await r.run(0)

    def test_repr(self):
        r = Retry(Pure(lambda x: x), 3)
        assert "Retry" in repr(r)
        assert "* 3" in repr(r)


class TestMul:
    async def test_retry_via_mul(self):
        attempts = []

        def flaky(_x):
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("once")
            return "ok"

        pipeline = Pure(flaky) * 3
        assert await pipeline.run(0) == "ok"

    def test_mul_with_non_int_returns_notimplemented(self):
        p = Pure(lambda x: x)
        assert p.__mul__("x") is NotImplemented

    def test_mul_with_zero_returns_notimplemented(self):
        p = Pure(lambda x: x)
        assert p.__mul__(0) is NotImplemented

    async def test_rmul(self):
        pipeline = 3 * Pure(lambda x: x + 1)
        assert await pipeline.run(1) == 2


# ── FailsWhen ───────────────────────────────────────────────────


class TestFailsWhen:
    async def test_predicate_triggers_failure(self):
        p = Pure(lambda x: "BAD")
        fw = FailsWhen(p, lambda r: r == "BAD")
        with pytest.raises(ValueError, match="predicate"):
            await fw.run(0)

    async def test_predicate_false_returns_result(self):
        p = Pure(lambda x: "OK")
        fw = FailsWhen(p, lambda r: r == "BAD")
        assert await fw.run(0) == "OK"

    def test_repr(self):
        p = Pure(lambda x: x)
        fw = FailsWhen(p, lambda r: False)
        assert "FailsWhen" in repr(fw)


class TestFailsWhenChained:
    async def test_via_method(self):
        p = Pure(lambda x: "BAD")
        wrapped = p.fails_when(lambda r: r == "BAD")
        with pytest.raises(ValueError):
            await wrapped.run(0)


# ── Router ──────────────────────────────────────────────────────


class TestRouter:
    async def test_routes_by_key(self):
        r = Router({"a": Pure(lambda x: "A"), "b": Pure(lambda x: "B")})
        assert await r.run("a") == "A"
        assert await r.run("b") == "B"

    async def test_tuple_input(self):
        # (key, payload) tuple — payload routed to the branch.
        r = Router({"a": Pure(lambda x: f"got {x}")})
        assert await r.run(("a", 42)) == "got 42"

    async def test_unknown_key_raises(self):
        r = Router({"a": Pure(lambda x: "A")})
        with pytest.raises(KeyError, match="No route"):
            await r.run("z")

    async def test_default_branch(self):
        r = Router(
            {
                "a": Pure(lambda x: "A"),
                "_default": Pure(lambda x: f"default-{x}"),
            }
        )
        assert await r.run("z") == "default-z"
        assert await r.run("a") == "A"

    def test_repr(self):
        r = Router({"a": Pure(lambda x: x)})
        assert "Router" in repr(r)

    def test_repr_with_default(self):
        r = Router(
            {
                "a": Pure(lambda x: x),
                "_default": Pure(lambda x: x),
            }
        )
        assert "_default" in repr(r)


# ── map / contramap ─────────────────────────────────────────────


class TestProfunctorMaps:
    async def test_map(self):
        p = Pure(lambda x: x + 1)
        mapped = p.map(lambda y: y * 10)
        assert await mapped.run(1) == 20

    async def test_contramap(self):
        p = Pure(lambda x: x * 2)
        cmapped = p.contramap(lambda y: y + 100)
        assert await cmapped.run(1) == 202  # (1+100)*2


# ── PipelineIterator ────────────────────────────────────────────


class TestPipelineIterator:
    async def test_iterate_feeds_output_back(self):
        # Pipeline: append "!" each call.
        p = Pure(lambda x: f"{x}!")
        it = p.iterate("start")
        results = []
        count = 0
        async for r in it:
            results.append(r)
            count += 1
            if count >= 3:
                break
        assert results == ["start!", "start!!", "start!!!"]

    async def test_feed_overrides_next_input(self):
        p = Pure(lambda x: f"{x}!")
        it = p.iterate("start")
        results = []
        first = await it.__anext__()
        results.append(first)
        # Override next input.
        it.feed("override")
        second = await it.__anext__()
        results.append(second)
        # Feed value used.
        assert "override!" in second

    async def test_aiter_returns_self(self):
        p = Pure(lambda x: x)
        it = p.iterate("x")
        assert it.__aiter__() is it


# ── Sentinel ─────────────────────────────────────────────────────


class TestSentinel:
    def test_sentinel_distinct_from_plausible_user_values(self):
        # ``_SENTINEL`` marks "no feed override" in PipelineIterator, so
        # it MUST be distinguishable (by identity) from every value a
        # caller could legitimately feed — including None, "", 0, False.
        for plausible in (None, "", 0, False, [], {}):
            assert _SENTINEL is not plausible


# ── Runnable protocol ────────────────────────────────────────────


class TestRunnableProtocol:
    def test_pure_satisfies(self):
        p = Pure(lambda x: x)
        assert isinstance(p, Runnable)


# ── Effects attribute ────────────────────────────────────────────


class TestEffectsAttribute:
    def test_default_none(self):
        b = BaseRunnable()
        assert b.effects is None
