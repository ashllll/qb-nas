"""BatchOptimizer — 分类批次优化"""
from __future__ import annotations

import re
from typing import Dict, List


class BatchOptimizer:
    def __init__(self, batch_size: int = 20, max_batch_size: int = 50):
        self.batch_size = batch_size
        self.max_batch_size = max_batch_size

    def optimize_batch(self, items: List[dict]) -> List[List[dict]]:
        if not items:
            return []

        items_with_priority = []
        for item in items:
            name = item.get("name", "")
            priority = self._calculate_priority(name)
            items_with_priority.append((priority, item, name))

        items_with_priority.sort(key=lambda x: x[0], reverse=True)

        high_priority = []
        medium_priority = []
        low_priority = []

        for priority, item, name in items_with_priority:
            if priority > 5:
                high_priority.append(item)
            elif priority > 2:
                medium_priority.append(item)
            else:
                low_priority.append(item)

        batches = []

        for group in [high_priority, medium_priority, low_priority]:
            for i in range(0, len(group), self.batch_size):
                batch = group[i:i + self.batch_size]
                if batch:
                    batches.append(batch)

        return batches

    def _calculate_priority(self, name: str) -> int:
        name_lower = name.lower()
        priority = 0

        if any(kw in name_lower for kw in ['s01e', 'season', 'ep01', '第', '集']):
            priority += 5
        if any(kw in name_lower for kw in ['动漫', 'anime', 'ova', 'bd']):
            priority += 4
        if any(kw in name_lower for kw in ['movie', 'film', '2024', '2023', '2025']):
            priority += 3
        if any(kw in name_lower for kw in ['game', 'software', 'crack']):
            priority += 3
        if any(kw in name_lower for kw in ['flac', 'mp3', 'album', 'ost']):
            priority += 2

        if re.search(r'2160p|4k|blu-?ray|bluray', name_lower):
            priority += 1
        if re.search(r'complete|全|完整|全集', name_lower):
            priority += 1

        return priority
