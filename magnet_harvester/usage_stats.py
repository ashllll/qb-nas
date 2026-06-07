"""UsageStats — 用量追踪，全项目共享"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class UsageStats:
    input_tokens:  int = 0
    output_tokens: int = 0
    api_calls:     int = 0
    errors:        int = 0
    start_time:    float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict:
        return {
            "input_tokens":       self.input_tokens,
            "output_tokens":      self.output_tokens,
            "total_tokens":       self.total_tokens,
            "api_calls":          self.api_calls,
            "errors":             self.errors,
            "elapsed_sec":        round(time.time() - self.start_time, 1),
            "estimated_cost_cny": round(self.total_tokens / 1_000_000 * 4, 4),
        }
