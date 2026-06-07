"""Local fallback classification rules — 独立于 API 调用的本地分类"""
from __future__ import annotations

import re
from typing import List, Tuple

LOCAL_RULES: List[Tuple[str, str]] = [
    # 综艺 — 具体节目名优先于结构模式
    (r'综艺|真人秀|选秀|variety.show|reality.show|脱口秀|奇葩说', "综艺"),
    # 电视剧 — 集数标注
    (r's\d{1,2}e\d{1,2}|season\s*\d|第[一二三四五六七八九十\d]+季|第.{1,4}集|ep\d+|全\d+集|第\d+部', "电视剧"),
    # 动漫
    (r'动漫|动画|anime|ova\b|bangumi|字幕组|[简繁]体字幕|国漫|日漫|剧场版', "动漫"),
    # 音乐
    (r'flac|mp3|aac|wav|dff|dsd|专辑|单曲|ost\b|soundtrack|album\b|hires|hi-res|黑胶', "音乐"),
    # 游戏
    (r'\bgame\b|goty|dlc\b|repack|codex|skidrow|fitgirl|gog\b|steam|破解版', "游戏"),
    # 软件
    (r'setup\.exe|installer|crack|keygen|adobe\s|office\s|v\d+\.\d+\.\d+|绿色版|便携版', "软件"),
    # 纪录片
    (r'documentary|纪录片|bbc\b|national.geo|discovery\b|探索频道|国家地理', "纪录片"),
]

VALID_CATEGORIES = ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]


def classify_local(name: str) -> str:
    """使用本地规则分类"""
    n = name.lower()
    for pattern, cat in LOCAL_RULES:
        if re.search(pattern, n, re.IGNORECASE):
            return cat
    return "电影"


def make_fallback(name: str, reason: str = "local_fallback") -> dict:
    """生成兜底分类结果（save_path 为空，由下载时动态解析）"""
    cat = classify_local(name)
    return {
        "category": cat,
        "confidence": "low",
        "reason": reason,
        "save_path": cat,
    }
