"""
Database caching layer to reduce redundant queries.
Cache is stored in memory and refreshed periodically or on-demand.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from bot.db import (
    get_source as _get_source,
    get_patterns as _get_patterns,
    get_footer as _get_footer,
    get_sources as _get_sources
)
from bot.model import SourceDisplay


class DBCache:
    """In-memory cache for frequently accessed database objects."""

    def __init__(self, cache_duration_minutes: int = 30) -> None:
        self.cache_duration: timedelta = timedelta(minutes=cache_duration_minutes)

        # Cache storage
        self._sources: Dict[int, SourceDisplay] = {}
        self._patterns: Dict[int, List[str]] = {}
        self._footers: Dict[int, Optional[str]] = {}

        # Cache timestamps
        self._sources_last_updated: Optional[datetime] = None
        self._patterns_last_updated: Dict[int, datetime] = {}
        self._footers_last_updated: Dict[int, datetime] = {}

    def _is_expired(self, last_updated: Optional[datetime]) -> bool:
        """Check if cache entry has expired."""
        if last_updated is None:
            return True
        return datetime.now() - last_updated > self.cache_duration

    async def get_source(self, channel_id: int, force_refresh: bool = False) -> SourceDisplay:
        """Get source with caching. Fetches all sources on first call."""
        if force_refresh or self._is_expired(self._sources_last_updated):
            await self.refresh_sources()

        if channel_id not in self._sources:
            # Single source not in cache, fetch it specifically
            source: SourceDisplay = await _get_source(channel_id)
            self._sources[channel_id] = source
            return source

        return self._sources[channel_id]

    async def refresh_sources(self) -> None:
        """Refresh all sources at once."""
        logging.info("Refreshing sources cache")
        self._sources = await _get_sources()
        self._sources_last_updated = datetime.now()

    async def get_patterns(self, channel_id: int, force_refresh: bool = False) -> List[str]:
        """Get patterns for a channel with caching."""
        last_updated: Optional[datetime] = self._patterns_last_updated.get(channel_id)

        if force_refresh or self._is_expired(last_updated):
            logging.info(f"Refreshing patterns cache for channel {channel_id}")
            patterns: List[str] = await _get_patterns(channel_id)
            self._patterns[channel_id] = patterns
            self._patterns_last_updated[channel_id] = datetime.now()
            return patterns

        return self._patterns.get(channel_id, [])

    async def get_footer(self, channel_id: int, force_refresh: bool = False) -> Optional[str]:
        """Get footer for a channel with caching."""
        last_updated: Optional[datetime] = self._footers_last_updated.get(channel_id)

        if force_refresh or self._is_expired(last_updated):
            logging.info(f"Refreshing footer cache for channel {channel_id}")
            footer: Optional[str] = await _get_footer(channel_id)
            self._footers[channel_id] = footer
            self._footers_last_updated[channel_id] = datetime.now()
            return footer

        return self._footers.get(channel_id)

    async def invalidate_source(self, channel_id: int) -> None:
        """Invalidate cache for a specific source."""
        if channel_id in self._sources:
            del self._sources[channel_id]
        self._sources_last_updated = None

    async def invalidate_patterns(self, channel_id: int) -> None:
        """Invalidate patterns cache for a specific channel."""
        if channel_id in self._patterns:
            del self._patterns[channel_id]
        if channel_id in self._patterns_last_updated:
            del self._patterns_last_updated[channel_id]

    async def invalidate_footer(self, channel_id: int) -> None:
        """Invalidate footer cache for a specific channel."""
        if channel_id in self._footers:
            del self._footers[channel_id]
        if channel_id in self._footers_last_updated:
            del self._footers_last_updated[channel_id]

    def clear_all(self) -> None:
        """Clear entire cache."""
        logging.info("Clearing all caches")
        self._sources.clear()
        self._patterns.clear()
        self._footers.clear()
        self._sources_last_updated = None
        self._patterns_last_updated.clear()
        self._footers_last_updated.clear()


# Global cache instance
_cache: Optional[DBCache] = None


def get_cache(cache_duration_minutes: int = 30) -> DBCache:
    """Get or create the global cache instance."""
    global _cache
    if _cache is None:
        _cache = DBCache(cache_duration_minutes=cache_duration_minutes)
    return _cache
