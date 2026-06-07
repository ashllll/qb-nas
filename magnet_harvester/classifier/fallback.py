"""Local fallback classification rules — 独立于 API 调用的本地分类"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from magnet_harvester.config import settings

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

QUALITY_PATTERNS: List[Tuple[str, str]] = [
    (r'2160p|4k|uhd|3840', '4K'),
    (r'1080p|full\s*hd|fhd|1920', '1080P'),
    (r'720p|hd\s*ready|1280', '720P'),
    (r'576p|480p|sd\b|dvd', 'SD'),
]

RESOLUTION_PATTERNS: List[Tuple[str, str]] = [
    (r'hdr\d{0,2}|dolby\s*vision|dv\b', 'HDR'),
    (r'blu-?ray|bluray|bdrip|bdr', 'BluRay'),
    (r'web-?dl|webrip|netflix|hulu|amazon', 'WEB'),
    (r'hdtv|tvrip|dvb', 'TV'),
    (r'dvdrip|dvd', 'DVD'),
    (r'web-?dl\s*\d{4}|wb-?dl', 'WEB-DL'),
]

ALIASES: Dict[str, str] = {
    "movie": "电影", "movies": "电影", "film": "电影",
    "tv": "电视剧", "drama": "电视剧", "series": "电视剧",
    "anime": "动漫", "animation": "动漫", "cartoon": "动漫",
    "music": "音乐", "audio": "音乐", "album": "音乐",
    "game": "游戏", "games": "游戏",
    "software": "软件", "app": "软件",
    "variety": "综艺",
    "documentary": "纪录片", "doc": "纪录片",
    "other": "其他", "others": "其他",
}

VALID_CATEGORIES = ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]


def classify_local(name: str) -> str:
    """使用本地规则分类"""
    n = name.lower()
    for pattern, cat in LOCAL_RULES:
        if re.search(pattern, n, re.IGNORECASE):
            return cat
    return "电影"


def normalize(raw: str) -> str:
    """规范化分类名称"""
    raw = raw.strip().strip('"\'')
    if raw in settings.CATEGORY_PATHS:
        return raw
    return ALIASES.get(raw.lower().replace(" ", ""), "其他")


def make_fallback(name: str, reason: str = "local_fallback") -> dict:
    """生成兜底分类结果（save_path 为空，由下载时动态解析）"""
    cat = classify_local(name)
    return {
        "category": cat,
        "confidence": "low",
        "reason": reason,
        "save_path": cat,  # 仅作分类名标识，实际路径由 qB 默认路径 + 分类名决定
    }


def classify_local_with_confidence(name: str) -> Tuple[str, str]:
    """本地分类并估算置信度"""
    n = name.lower()
    matched_rules = []

    for pattern, cat in LOCAL_RULES:
        if re.search(pattern, n, re.IGNORECASE):
            matched_rules.append((pattern, cat))

    if not matched_rules:
        return "电影", "medium"

    primary_category = matched_rules[0][1]

    if len(matched_rules) >= 2:
        return primary_category, "high"
    elif any(kw in n for kw in ['complete', '全', '完整', '全集']):
        return primary_category, "high"
    else:
        return primary_category, "medium"


def analyze_quality(name: str) -> dict:
    """分析文件名中的画质信息"""
    n = name.lower()
    quality = None
    source = None

    for pattern, q in QUALITY_PATTERNS:
        if re.search(pattern, n):
            quality = q
            break

    for pattern, s in RESOLUTION_PATTERNS:
        if re.search(pattern, n):
            source = s
            break

    return {"quality": quality, "source": source}
