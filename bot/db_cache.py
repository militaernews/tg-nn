"""
Database caching layer to reduce redundant queries.
Cache is stored in memory and refreshed only on-demand via /refresh command.
Optimized for zero DB calls during message processing.
"""
import logging
import time
from typing import Dict, List, Optional, Tuple

from bot.db import (
    get_source as _get_source,
    get_patterns as _get_patterns,
    get_footer as _get_footer,
    get_sources as _get_sources,
    get_destinations as _get_destinations
)
from bot.model import SourceDisplay, Destination


class DBCache:
    """In-memory cache for frequently accessed database objects."""

    def __init__(self) -> None:
        # Cache storage
        self._sources: Dict[int, SourceDisplay] = {}
        self._patterns: Dict[int, List[str]] = {}
        self._footers: Dict[int, Optional[str]] = {}
        self._destinations: List[Destination] = []
        self._destination_map: Dict[str, int] = {}  # Pre-computed name->id map
        self._destination_regions: List[str] = []    # Pre-computed region list

        # Track if cache has been initialized
        self._initialized: bool = False

        # Message deduplication cache (prevents processing duplicates)
        self._recent_messages: Dict[Tuple[int, int], float] = {}

    def is_duplicate_message(self, chat_id: int, message_id: int) -> bool:
        """Check if message was recently processed (prevents duplicates)."""
        key = (chat_id, message_id)
        now = time.time()

        # Clean old entries (older than 10 seconds)
        self._recent_messages = {
            k: v for k, v in self._recent_messages.items()
            if now - v < 10
        }

        if key in self._recent_messages:
            return True

        self._recent_messages[key] = now
        return False

    async def get_source(self, channel_id: int) -> SourceDisplay:
        """Get source from cache. Returns cached value or fetches if not initialized."""
        if not self._initialized:
            await self.warm_cache()

        # Fast dict lookup using .get()
        source = self._sources.get(channel_id)
        if source is None:
            # Single source not in cache, fetch it specifically
            logging.info(f"Source {channel_id} not in cache, fetching individually")
            source = await _get_source(channel_id)
            self._sources[channel_id] = source

        return source

    async def refresh_sources(self) -> None:
        """Refresh all sources from database."""
        logging.info("Refreshing sources cache from database")
        self._sources = await _get_sources()
        logging.info(f"Loaded {len(self._sources)} sources into cache")

    async def get_destinations(self) -> List[Destination]:
        """Get all destinations from cache."""
        if not self._initialized:
            await self.warm_cache()

        return self._destinations

    def get_destination_map(self) -> Dict[str, int]:
        """Get pre-computed destination name->id mapping (synchronous, no await)."""
        return self._destination_map

    def get_destination_regions(self) -> List[str]:
        """Get pre-computed list of region names (synchronous, no await)."""
        return self._destination_regions

    async def refresh_destinations(self) -> None:
        """Refresh destinations from database and pre-compute mappings."""
        logging.info("Refreshing destinations cache from database")
        self._destinations = await _get_destinations()

        # Pre-compute mappings for O(1) lookups in routing
        self._destination_map = {d.name.lower(): d.channel_id for d in self._destinations}
        self._destination_regions = list(self._destination_map.keys())
        logging.info(f"Pre-computed destination map with {len(self._destination_map)} regions: {self._destination_regions}")

    async def get_patterns(self, channel_id: int) -> List[str]:
        """Get patterns for a channel from cache."""
        # Fast dict lookup using .get()
        patterns = self._patterns.get(channel_id)
        if patterns is None:
            logging.info(f"Patterns for {channel_id} not in cache, fetching")
            patterns = await _get_patterns(channel_id)
            self._patterns[channel_id] = patterns

        return patterns

    async def refresh_patterns(self, channel_id: int) -> None:
        """Refresh patterns for a specific channel."""
        logging.info(f"Refreshing patterns cache for channel {channel_id}")
        patterns: List[str] = await _get_patterns(channel_id)
        self._patterns[channel_id] = patterns

    async def get_footer(self, channel_id: int) -> Optional[str]:
        """Get footer for a channel from cache."""
        # Check if already cached
        if channel_id in self._footers:
            return self._footers[channel_id]

        # Not cached, fetch it
        logging.info(f"Footer for {channel_id} not in cache, fetching")
        footer: Optional[str] = await _get_footer(channel_id)
        self._footers[channel_id] = footer
        return footer

    async def refresh_footer(self, channel_id: int) -> None:
        """Refresh footer for a specific channel."""
        logging.info(f"Refreshing footer cache for channel {channel_id}")
        footer: Optional[str] = await _get_footer(channel_id)
        self._footers[channel_id] = footer

    async def warm_cache(self) -> None:
        """Pre-warm cache with commonly accessed data."""
        start_time = time.perf_counter()
        logging.info("Warming cache...")

        await self.refresh_sources()
        await self.refresh_destinations()

        self._initialized = True
        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Cache warmed in {elapsed:.2f}ms: {len(self._sources)} sources, {len(self._destinations)} destinations")

    async def refresh_all(self) -> None:
        """Refresh all cached data from database."""
        start_time = time.perf_counter()
        logging.info("Refreshing all caches from database")

        await self.refresh_sources()
        await self.refresh_destinations()

        self._initialized = True
        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"All caches refreshed in {elapsed:.2f}ms")

    async def invalidate_source(self, channel_id: int) -> None:
        """Invalidate cache for a specific source."""
        if channel_id in self._sources:
            del self._sources[channel_id]
            logging.info(f"Invalidated source cache for {channel_id}")

    async def invalidate_patterns(self, channel_id: int) -> None:
        """Invalidate patterns cache for a specific channel."""
        if channel_id in self._patterns:
            del self._patterns[channel_id]
            logging.info(f"Invalidated patterns cache for {channel_id}")

    async def invalidate_footer(self, channel_id: int) -> None:
        """Invalidate footer cache for a specific channel."""
        if channel_id in self._footers:
            del self._footers[channel_id]
            logging.info(f"Invalidated footer cache for {channel_id}")

    def clear_all(self) -> None:
        """Clear entire cache."""
        logging.info("Clearing all caches")
        self._sources.clear()
        self._patterns.clear()
        self._footers.clear()
        self._destinations.clear()
        self._destination_map.clear()
        self._destination_regions.clear()
        self._recent_messages.clear()
        self._initialized = False


# Global cache instance
_cache: Optional[DBCache] = None


def get_cache() -> DBCache:
    """Get or create the global cache instance."""
    global _cache
    if _cache is None:
        _cache = DBCache()
    return _cache