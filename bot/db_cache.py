"""
Database caching layer to reduce redundant queries.
Cache is stored in memory and refreshed only on-demand via /refresh command.
Optimized for zero DB calls during message processing.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from bot.db import (
    get_source as _get_source,
    get_patterns as _get_patterns,
    get_footer as _get_footer,
    get_sources as _get_sources,
    get_destinations as _get_destinations,
)
from bot.model import SourceDisplay, Destination


class DBCache:
    """In-memory cache for frequently accessed database objects."""

    def __init__(self) -> None:
        self._sources: Dict[int, SourceDisplay] = {}
        self._patterns: Dict[int, List[str]] = {}
        self._footers: Dict[int, Optional[str]] = {}
        self._destinations: List[Destination] = []
        self._destination_map: Dict[str, int] = {}
        self._destination_regions: List[str] = []
        self._initialized: bool = False
        self._lock = asyncio.Lock()

        # Deduplication: (chat_id, message_id) -> timestamp
        self._recent_messages: Dict[Tuple[int, int], float] = {}
        self._DEDUP_TTL = 10.0  # seconds

    # ── Deduplication ────────────────────────────────────────────────────────

    def is_duplicate_message(self, chat_id: int, message_id: int) -> bool:
        """Return True if this (chat_id, message_id) was seen within DEDUP_TTL."""
        key = (chat_id, message_id)
        now = time.monotonic()  # monotonic is cheaper & safer than time.time()

        # Evict stale entries
        self._recent_messages = {
            k: v for k, v in self._recent_messages.items()
            if now - v < self._DEDUP_TTL
        }

        if key in self._recent_messages:
            return True

        self._recent_messages[key] = now
        return False

    # ── Internal warm / refresh ───────────────────────────────────────────────

    async def _ensure_initialized(self) -> None:
        """Guarantee the cache is warmed exactly once, even under concurrency."""
        if self._initialized:
            return
        async with self._lock:
            if not self._initialized:   # double-checked locking
                await self._refresh_all_unsafe()

    async def _refresh_all_unsafe(self) -> None:
        """Refresh everything concurrently. Must be called with _lock held (or at init)."""
        start = time.perf_counter()
        logging.info("Refreshing all caches from database")

        # Fan-out: fetch sources and destinations in parallel
        sources, destinations = await asyncio.gather(
            _get_sources(),
            _get_destinations(),
        )

        self._sources = sources
        self._destinations = destinations
        self._destination_map = {d.name.lower(): d.channel_id for d in destinations}
        self._destination_regions = list(self._destination_map.keys())
        self._initialized = True

        elapsed_ms = (time.perf_counter() - start) * 1000
        logging.info(
            f"Cache ready in {elapsed_ms:.1f}ms — "
            f"{len(self._sources)} sources, {len(self._destinations)} destinations"
        )

    # ── Public read API ───────────────────────────────────────────────────────

    async def get_source(self, channel_id: int) -> SourceDisplay:
        await self._ensure_initialized()
        source = self._sources.get(channel_id)
        if source is None:
            logging.info(f"Source {channel_id} not in cache, fetching individually")
            source = await _get_source(channel_id)
            self._sources[channel_id] = source
        return source

    async def get_destinations(self) -> List[Destination]:
        await self._ensure_initialized()
        return self._destinations

    def get_destination_map(self) -> Dict[str, int]:
        return self._destination_map

    def get_destination_regions(self) -> List[str]:
        return self._destination_regions

    async def get_patterns(self, channel_id: int) -> List[str]:
        await self._ensure_initialized()
        patterns = self._patterns.get(channel_id)
        if patterns is None:
            logging.info(f"Patterns for {channel_id} not in cache, fetching")
            patterns = await _get_patterns(channel_id)
            self._patterns[channel_id] = patterns
        return patterns

    async def get_footer(self, channel_id: int) -> Optional[str]:
        await self._ensure_initialized()
        if channel_id not in self._footers:
            logging.info(f"Footer for {channel_id} not in cache, fetching")
            self._footers[channel_id] = await _get_footer(channel_id)
        return self._footers[channel_id]

    # ── Public refresh API (called by /refresh command) ───────────────────────

    async def refresh_all(self) -> None:
        async with self._lock:
            await self._refresh_all_unsafe()

    async def refresh_sources(self) -> None:
        logging.info("Refreshing sources cache")
        async with self._lock:
            self._sources = await _get_sources()
        logging.info(f"Loaded {len(self._sources)} sources")

    async def refresh_destinations(self) -> None:
        logging.info("Refreshing destinations cache")
        async with self._lock:
            destinations = await _get_destinations()
            self._destinations = destinations
            self._destination_map = {d.name.lower(): d.channel_id for d in destinations}
            self._destination_regions = list(self._destination_map.keys())
        logging.info(f"Loaded {len(self._destinations)} destinations")

    async def refresh_patterns(self, channel_id: int) -> None:
        logging.info(f"Refreshing patterns for channel {channel_id}")
        self._patterns[channel_id] = await _get_patterns(channel_id)

    async def refresh_footer(self, channel_id: int) -> None:
        logging.info(f"Refreshing footer for channel {channel_id}")
        self._footers[channel_id] = await _get_footer(channel_id)

    # ── Invalidation ──────────────────────────────────────────────────────────

    def invalidate_source(self, channel_id: int) -> None:
        self._sources.pop(channel_id, None)

    def invalidate_patterns(self, channel_id: int) -> None:
        self._patterns.pop(channel_id, None)

    def invalidate_footer(self, channel_id: int) -> None:
        self._footers.pop(channel_id, None)

    def clear_all(self) -> None:
        logging.info("Clearing all caches")
        self._sources.clear()
        self._patterns.clear()
        self._footers.clear()
        self._destinations.clear()
        self._destination_map.clear()
        self._destination_regions.clear()
        self._recent_messages.clear()
        self._initialized = False


# ── Singleton ─────────────────────────────────────────────────────────────────

_cache: Optional[DBCache] = None


def get_cache() -> DBCache:
    global _cache
    if _cache is None:
        _cache = DBCache()
    return _cache