"""30s TTL LRU cache with per-key asyncio.Lock single-flight.

Plan §4.3 AC#4 / spec §3.2 Q5-B. Cache layer for metadata-only FastGPT
read-only calls (list_collections / get_collection_files). The streaming
binary download path is intentionally bypassed — pushing raw bytes through
an in-memory cache would blow up RAM on large files (plan §4.3 AC#4 字面
"不缓存" + risk §5.6); AC#4 also asserts via grep that this module never
references the streaming method by name.

Health check is also bypassed (plan §4.3 AC#6) — it must reflect the
real-time reachability of the upstream, not a memoised result.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable


class TTLCache:
    """Async-safe in-memory cache with TTL eviction + LRU bound.

    Concurrency: ``get_or_fetch`` takes a per-key asyncio.Lock so N
    concurrent misses for the same key collapse into one upstream call.
    Stampede on different keys is unaffected (per-key locks, not global).

    Bound: when ``len(store) >= max_size`` we evict the least-recently-
    used entry via ``OrderedDict.popitem(last=False)`` — O(1) per insert,
    matching the polish landed in B-NEW-48 (CLEAN-2 mode_dispatcher).
    """

    def __init__(self, ttl_seconds: float = 30.0, max_size: int = 256) -> None:
        self._ttl = float(ttl_seconds)
        self._max = int(max_size)
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _now(self) -> float:
        return time.monotonic()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    def _try_hit(self, key: str) -> tuple[bool, Any]:
        entry = self._store.get(key)
        if entry is None:
            return False, None
        inserted_at, value = entry
        if self._now() - inserted_at >= self._ttl:
            self._store.pop(key, None)
            return False, None
        self._store.move_to_end(key)
        return True, value

    def _put(self, key: str, value: Any) -> None:
        if key in self._store:
            self._store.pop(key)
        self._store[key] = (self._now(), value)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    async def get_or_fetch(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[Any]],
    ) -> Any:
        hit, value = self._try_hit(key)
        if hit:
            return value
        lock = await self._lock_for(key)
        async with lock:
            hit, value = self._try_hit(key)
            if hit:
                return value
            value = await fetcher()
            self._put(key, value)
            return value

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)
