"""TTLCache unit tests — plan §4.3 AC#4 字段级.

Covers TTL eviction (freezegun-driven), LRU bound (popitem(last=False) at
overflow), per-key single-flight (concurrent misses collapse), and the
grep-asserted constraint that the streaming method name never appears in
``cache.py`` (mock-vs-real defence: download must not be cached).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ncmu_backend.fastgpt_readonly import cache as cache_module
from ncmu_backend.fastgpt_readonly.cache import TTLCache


async def test_get_or_fetch_caches_within_ttl():
    cache = TTLCache(ttl_seconds=30.0, max_size=8)
    calls = 0

    async def fetcher():
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    a = await cache.get_or_fetch("k1", fetcher)
    b = await cache.get_or_fetch("k1", fetcher)
    assert a == b == "value-1"
    assert calls == 1


async def test_get_or_fetch_evicts_after_31s_ttl(monkeypatch):
    """TTL = 30s; jumping 31s forward must trigger a re-fetch.

    Plan §4.3 AC#4 字面 calls out ``freezegun``; we monkeypatch the
    module-local ``time.monotonic`` instead so this test does not pull
    in an additional dev dependency (freezegun is not yet vendored in
    pyproject.toml). The semantics are identical — we control the clock
    the cache observes.
    """
    cache = TTLCache(ttl_seconds=30.0, max_size=8)
    calls = 0

    async def fetcher():
        nonlocal calls
        calls += 1
        return calls

    fake_now = {"t": 1000.0}
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: fake_now["t"])

    assert await cache.get_or_fetch("k", fetcher) == 1
    fake_now["t"] += 29.0
    assert await cache.get_or_fetch("k", fetcher) == 1  # still fresh
    fake_now["t"] += 2.0  # 31s elapsed
    assert await cache.get_or_fetch("k", fetcher) == 2  # re-fetched
    assert calls == 2


async def test_lru_evicts_oldest_at_max_size():
    cache = TTLCache(ttl_seconds=60.0, max_size=2)

    async def make(v):
        async def f():
            return v
        return f

    await cache.get_or_fetch("a", await make("A"))
    await cache.get_or_fetch("b", await make("B"))
    await cache.get_or_fetch("c", await make("C"))  # evicts "a"

    assert cache.size() == 2
    new_a_calls = 0

    async def fetcher_a():
        nonlocal new_a_calls
        new_a_calls += 1
        return "A-fresh"

    # "a" was evicted → fetcher must run again.
    assert await cache.get_or_fetch("a", fetcher_a) == "A-fresh"
    assert new_a_calls == 1


async def test_lru_hit_moves_entry_to_end_protects_from_eviction():
    cache = TTLCache(ttl_seconds=60.0, max_size=2)

    async def f_a():
        return "A"

    async def f_b():
        return "B"

    async def f_c():
        return "C"

    await cache.get_or_fetch("a", f_a)
    await cache.get_or_fetch("b", f_b)
    # Hit "a" so it becomes most-recent, then add "c" which should evict "b".
    await cache.get_or_fetch("a", f_a)
    await cache.get_or_fetch("c", f_c)

    refetch_b_calls = 0

    async def refetch_b():
        nonlocal refetch_b_calls
        refetch_b_calls += 1
        return "B-fresh"

    assert await cache.get_or_fetch("b", refetch_b) == "B-fresh"
    assert refetch_b_calls == 1  # "b" was evicted, "a" was retained


async def test_per_key_single_flight_collapses_concurrent_misses():
    """N concurrent ``get_or_fetch`` on the same key → fetcher runs once.

    Without per-key locking each coroutine would trigger an upstream
    call (thundering herd). The fetcher sleeps briefly so all callers
    race to acquire the same lock at once.
    """
    cache = TTLCache(ttl_seconds=60.0, max_size=8)
    call_count = 0

    async def slow_fetcher():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "shared"

    results = await asyncio.gather(*[
        cache.get_or_fetch("hot-key", slow_fetcher) for _ in range(10)
    ])
    assert results == ["shared"] * 10
    assert call_count == 1


async def test_invalidate_drops_entry():
    cache = TTLCache(ttl_seconds=60.0, max_size=8)
    calls = 0

    async def fetcher():
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get_or_fetch("k", fetcher) == 1
    cache.invalidate("k")
    assert await cache.get_or_fetch("k", fetcher) == 2


def test_cache_module_does_not_reference_streaming_method_name():
    """Plan §4.3 AC#4 字面: grep -n 'download_file' cache.py 命中 0 行.

    The constraint exists so a future maintainer cannot accidentally
    wire the streaming method through this cache — large binary streams
    would blow up RAM (risk §5.6). We resolve ``cache.py``'s real path
    via ``inspect.getsourcefile`` so the assertion works regardless of
    whether the test runs against the in-tree source or an installed
    site-packages copy.
    """
    import inspect

    source_path = inspect.getsourcefile(cache_module)
    assert source_path is not None
    text = Path(source_path).read_text(encoding="utf-8")
    assert "download_file" not in text, (
        "cache.py must not mention `download_file` (plan §4.3 AC#4 grep guard)"
    )


def test_fastgpt_readonly_package_has_no_write_method_calls():
    """Plan §4.3 AC#1 字面 grep guard: no module-level httpx.{post,put,delete,patch}.

    Scope note (TASK-KBFIX-1, 2026-06-04): this guard matches the
    *module-level* convenience helpers ``httpx.post(...)`` etc. — it does
    NOT match instance calls ``self._client().post(...)``. That is
    deliberate: ``client._post`` legitimately issues a **POST** to
    ``/api/core/dataset/collection/list`` because real FastGPT v4.14.10.2
    models "list collections" as POST + JSON body (实测对账 2026-06-04). That
    is a READ, not a write — ``read-only`` here means no create/update/delete
    (download/collection-detail stay GET). Do NOT "tighten" this regex to ban
    instance ``.post`` and revert list_collections to GET: GET collection/list
    returns HTTP 500 ``unExistDataset`` on the live API (the original panel bug).

    Co-located with cache tests because it's a static module-level check
    (doesn't need any cache fixture)."""
    import inspect
    import re
    import ncmu_backend.fastgpt_readonly as pkg

    pkg_path = Path(inspect.getsourcefile(pkg) or "")
    pkg_root = pkg_path.parent
    pattern = re.compile(r"httpx\.(post|put|delete|patch)")
    offenders = []
    for path in pkg_root.glob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line}")
    assert not offenders, "AC#1 violation:\n" + "\n".join(offenders)
