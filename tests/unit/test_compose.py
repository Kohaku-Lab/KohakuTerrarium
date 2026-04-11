"""Unit tests for the composition algebra — pure functions only, no agents."""

import pytest

from kohakuterrarium.compose import (
    BaseRunnable,
    Effects,
    Product,
    Pure,
    Retry,
    Router,
    Sequence,
)

# ── Helpers ──────────────────────────────────────────────────────────


class Add(BaseRunnable):
    def __init__(self, n: int):
        self.n = n

    async def run(self, input):
        return input + self.n


class Mul(BaseRunnable):
    def __init__(self, n: int):
        self.n = n

    async def run(self, input):
        return input * self.n


class Fail(BaseRunnable):
    """Always raises."""

    def __init__(self, times: int = 999):
        self._times = times
        self._count = 0

    async def run(self, input):
        self._count += 1
        if self._count <= self._times:
            raise RuntimeError(f"fail #{self._count}")
        return input


class Echo(BaseRunnable):
    async def run(self, input):
        return input


# ── Pure ─────────────────────────────────────────────────────────────


class TestPure:
    @pytest.mark.asyncio
    async def test_sync_callable(self):
        p = Pure(lambda x: x * 2)
        assert await p.run(5) == 10

    @pytest.mark.asyncio
    async def test_async_callable(self):
        async def double(x):
            return x * 2

        p = Pure(double)
        assert await p.run(5) == 10

    @pytest.mark.asyncio
    async def test_builtin_callable(self):
        p = Pure(str.upper)
        assert await p.run("hello") == "HELLO"

    def test_non_callable_raises(self):
        with pytest.raises(TypeError):
            Pure(42)


# ── Sequence (>>) ────────────────────────────────────────────────────


class TestSequence:
    @pytest.mark.asyncio
    async def test_two_steps(self):
        pipeline = Add(1) >> Add(2)
        assert await pipeline.run(0) == 3

    @pytest.mark.asyncio
    async def test_three_steps(self):
        pipeline = Add(1) >> Mul(2) >> Add(10)
        assert await pipeline.run(0) == 12  # (0+1)*2+10

    @pytest.mark.asyncio
    async def test_flatten(self):
        pipeline = Add(1) >> Add(2) >> Add(3)
        assert isinstance(pipeline, Sequence)
        assert len(pipeline._steps) == 3  # flat, not nested

    @pytest.mark.asyncio
    async def test_auto_wrap_callable(self):
        pipeline = Add(1) >> (lambda x: x * 10)
        assert await pipeline.run(2) == 30  # (2+1)*10

    @pytest.mark.asyncio
    async def test_auto_wrap_chain(self):
        pipeline = Add(1) >> str >> str.upper
        assert await pipeline.run(5) == "6"  # str(6).upper() but int->str

    @pytest.mark.asyncio
    async def test_rshift_dict_creates_router(self):
        classifier = Pure(lambda x: "even" if x % 2 == 0 else "odd")
        pipeline = classifier >> {
            "even": Pure(lambda _: "is even"),
            "odd": Pure(lambda _: "is odd"),
        }
        assert await pipeline.run(4) == "is even"
        assert await pipeline.run(3) == "is odd"


# ── Parallel (&) ─────────────────────────────────────────────────────


class TestParallel:
    @pytest.mark.asyncio
    async def test_two_branches(self):
        pipeline = Add(1) & Mul(2)
        result = await pipeline.run(5)
        assert result == (6, 10)

    @pytest.mark.asyncio
    async def test_three_branches(self):
        pipeline = Add(1) & Add(2) & Add(3)
        result = await pipeline.run(0)
        assert result == (1, 2, 3)

    @pytest.mark.asyncio
    async def test_flatten(self):
        pipeline = Add(1) & Add(2) & Add(3)
        assert isinstance(pipeline, Product)
        assert len(pipeline._branches) == 3

    @pytest.mark.asyncio
    async def test_auto_wrap_callable(self):
        pipeline = Add(1) & (lambda x: x * 10)
        result = await pipeline.run(5)
        assert result == (6, 50)


# ── Fallback (|) ─────────────────────────────────────────────────────


class TestFallback:
    @pytest.mark.asyncio
    async def test_primary_succeeds(self):
        pipeline = Add(1) | Add(100)
        assert await pipeline.run(0) == 1  # primary wins

    @pytest.mark.asyncio
    async def test_primary_fails(self):
        pipeline = Fail() | Add(100)
        assert await pipeline.run(0) == 100  # fallback

    @pytest.mark.asyncio
    async def test_chain(self):
        pipeline = Fail() | Fail() | Add(42)
        assert await pipeline.run(0) == 42  # third try

    @pytest.mark.asyncio
    async def test_auto_wrap_callable(self):
        pipeline = Fail() | (lambda x: x + 99)
        assert await pipeline.run(1) == 100


class TestFailsWhen:
    @pytest.mark.asyncio
    async def test_predicate_triggers(self):
        pipeline = Echo().fails_when(lambda r: r < 0)
        with pytest.raises(ValueError):
            await pipeline.run(-1)

    @pytest.mark.asyncio
    async def test_predicate_passes(self):
        pipeline = Echo().fails_when(lambda r: r < 0)
        assert await pipeline.run(5) == 5

    @pytest.mark.asyncio
    async def test_with_fallback(self):
        pipeline = Echo().fails_when(lambda r: r < 0) | Pure(lambda _: 0)
        assert await pipeline.run(-1) == 0  # fallback triggered
        assert await pipeline.run(5) == 5  # no fallback


# ── Retry (* N) ──────────────────────────────────────────────────────


class TestRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first(self):
        pipeline = Add(1) * 3
        assert await pipeline.run(0) == 1

    @pytest.mark.asyncio
    async def test_succeeds_after_failures(self):
        failing = Fail(times=2)  # fails twice, then succeeds
        pipeline = failing * 3
        assert await pipeline.run(42) == 42

    @pytest.mark.asyncio
    async def test_exhausted(self):
        pipeline = Fail() * 2
        with pytest.raises(RuntimeError):
            await pipeline.run(0)

    @pytest.mark.asyncio
    async def test_rmul(self):
        pipeline = 3 * Add(1)
        assert isinstance(pipeline, Retry)


# ── Router ───────────────────────────────────────────────────────────


class TestRouter:
    @pytest.mark.asyncio
    async def test_key_match(self):
        router = Router(
            {
                "a": Pure(lambda _: "route_a"),
                "b": Pure(lambda _: "route_b"),
            }
        )
        assert await router.run("a") == "route_a"
        assert await router.run("b") == "route_b"

    @pytest.mark.asyncio
    async def test_tuple_input(self):
        router = Router({"greet": Pure(lambda p: f"Hello {p}")})
        assert await router.run(("greet", "World")) == "Hello World"

    @pytest.mark.asyncio
    async def test_missing_key_raises(self):
        router = Router({"a": Echo()})
        with pytest.raises(KeyError):
            await router.run("nope")

    @pytest.mark.asyncio
    async def test_default_route(self):
        router = Router({"a": Add(1), "_default": Add(99)})
        assert await router.run(("nope", 0)) == 99

    @pytest.mark.asyncio
    async def test_via_rshift_dict(self):
        pipeline = Pure(lambda x: ("double", x)) >> {
            "double": Mul(2),
            "triple": Mul(3),
        }
        assert await pipeline.run(5) == 10


# ── Iterate (async for) ─────────────────────────────────────────────


class TestIterate:
    @pytest.mark.asyncio
    async def test_basic_iteration(self):
        pipeline = Add(1)
        results = []
        async for result in pipeline.iterate(0):
            results.append(result)
            if len(results) >= 3:
                break
        assert results == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_feed_override(self):
        pipeline = Add(1)
        it = pipeline.iterate(0)
        r1 = await it.__anext__()
        assert r1 == 1
        it.feed(10)  # override next input
        r2 = await it.__anext__()
        assert r2 == 11  # 10 + 1, not 1 + 1

    @pytest.mark.asyncio
    async def test_convergence(self):
        pipeline = Pure(lambda x: x // 2)
        results = []
        async for result in pipeline.iterate(100):
            results.append(result)
            if result <= 1:
                break
        assert results[-1] <= 1


# ── __call__ ─────────────────────────────────────────────────────────


class TestCall:
    @pytest.mark.asyncio
    async def test_await_call(self):
        pipeline = Add(1) >> Mul(2)
        assert await pipeline(5) == 12  # (5+1)*2

    @pytest.mark.asyncio
    async def test_pure_call(self):
        p = Pure(str.upper)
        assert await p("hello") == "HELLO"


# ── map / contramap ──────────────────────────────────────────────────


class TestProfunctor:
    @pytest.mark.asyncio
    async def test_map(self):
        pipeline = Add(1).map(lambda x: x * 10)
        assert await pipeline.run(2) == 30  # (2+1)*10

    @pytest.mark.asyncio
    async def test_contramap(self):
        pipeline = Add(1).contramap(lambda x: x * 10)
        assert await pipeline.run(2) == 21  # (2*10)+1

    @pytest.mark.asyncio
    async def test_map_chain(self):
        pipeline = Add(1).map(str).map(lambda s: s + "!")
        assert await pipeline.run(5) == "6!"


# ── Complex compositions ─────────────────────────────────────────────


class TestComplex:
    @pytest.mark.asyncio
    async def test_parallel_then_reduce(self):
        pipeline = (Add(1) & Add(2) & Add(3)) >> Pure(sum)
        assert await pipeline.run(0) == 6  # 1+2+3

    @pytest.mark.asyncio
    async def test_retry_with_fallback(self):
        pipeline = (Fail() * 2) | Add(42)
        assert await pipeline.run(0) == 42

    @pytest.mark.asyncio
    async def test_sequence_with_parallel(self):
        pipeline = Add(1) >> (Mul(2) & Mul(3)) >> Pure(lambda t: t[0] + t[1])
        assert await pipeline.run(5) == 30  # (5+1)*2 + (5+1)*3 = 12+18


# ── Effects ──────────────────────────────────────────────────────────


class TestEffects:
    def test_sequential(self):
        a = Effects(cost=100, latency=2.0, reliability=0.95)
        b = Effects(cost=200, latency=3.0, reliability=0.90)
        c = a.sequential(b)
        assert c.cost == 300
        assert c.latency == 5.0
        assert abs(c.reliability - 0.855) < 0.001

    def test_parallel(self):
        a = Effects(cost=100, latency=2.0, reliability=0.95)
        b = Effects(cost=200, latency=3.0, reliability=0.90)
        c = a.parallel(b)
        assert c.cost == 300
        assert c.latency == 3.0
        assert abs(c.reliability - 0.855) < 0.001

    def test_none_propagates(self):
        a = Effects(cost=100)
        b = Effects(cost=None)
        c = a.sequential(b)
        assert c.cost is None


# ── repr ─────────────────────────────────────────────────────────────


class TestRepr:
    def test_sequence_repr(self):
        pipeline = Add(1) >> Add(2)
        assert "Sequence" in repr(pipeline)

    def test_product_repr(self):
        pipeline = Add(1) & Add(2)
        assert "Product" in repr(pipeline)

    def test_pure_repr(self):
        p = Pure(str.upper)
        assert "Pure" in repr(p)
        assert "upper" in repr(p)
