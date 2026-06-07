"""ClassificationCache — 分类结果缓存"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Dict, Optional


class ClassificationCache:
    def __init__(self, max_age_seconds: int = 3600):
        self._cache: Dict[str, dict] = {}
        self._timestamps: Dict[str, datetime] = {}
        self._max_age = timedelta(seconds=max_age_seconds)
        self._hits = 0
        self._misses = 0

    def _make_key(self, name: str) -> str:
        normalized = re.sub(r'[^\w\u4e00-\u9fff]', '', name.lower())
        return hashlib.md5(normalized.encode()).hexdigest()

    def get(self, name: str) -> Optional[dict]:
        key = self._make_key(name)
        if key in self._cache:
            if datetime.now() - self._timestamps[key] < self._max_age:
                self._hits += 1
                return self._cache[key].copy()
            else:
                del self._cache[key]
                del self._timestamps[key]
        self._misses += 1
        return None

    def put(self, name: str, category: str) -> None:
        """Simplified put: just stores a category string (for tests)"""
        key = self._make_key(name)
        self._cache[key] = {"category": category}
        self._timestamps[key] = datetime.now()

    def set(self, name: str, result: dict):
        key = self._make_key(name)
        self._cache[key] = result.copy()
        self._timestamps[key] = datetime.now()

    def invalidate(self, name: str):
        key = self._make_key(name)
        self._cache.pop(key, None)
        self._timestamps.pop(key, None)

    def clear(self):
        self._cache.clear()
        self._timestamps.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "hit_rate_percent": round(hit_rate, 1),
        }
