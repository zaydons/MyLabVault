"""Simple cache invalidation utilities."""

from typing import Dict, Any


class SimpleCache:
    """Simple cache for pattern-based invalidation."""

    def __init__(self):
        self.cache: Dict[str, Dict[str, Any]] = {}

    def clear(self) -> None:
        """Clear all cached entries."""
        self.cache.clear()

    def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all cache keys matching a pattern (uses startswith)."""
        keys_to_delete = [key for key in self.cache.keys() if key.startswith(pattern)]
        for key in keys_to_delete:
            del self.cache[key]
        return len(keys_to_delete)


# Global cache instance
api_cache = SimpleCache()