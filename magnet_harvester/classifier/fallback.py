"""Local fallback classification rules — 本地规则引擎"""
from __future__ import annotations

import re
from typing import List, Tuple

# ═══════════════════════════════════════════════════
# 分类规则（按优先级排列，先匹配先返回）
# ═══════════════════════════════════════════════════
LOCAL_RULES: List[Tuple[str, str]] = [
    # ── 综艺（在 E\d+ 之前，防止 Running.Man.E650 被误判为电视剧）──
    (r'综艺|真人秀|选秀|variety\s*show|reality\s*show|脱口秀|奇葩说|吐槽大会', "综艺"),
    (r'\brunning[\s.]man\b|\bkeep[\s.]running\b', "综艺"),

    # ── 纪录片（优先于电影格式）──
    (r'documentary|纪录片|\bbbc\b|national\s*geo|discovery\b|探索频道|国家地理|历史频道', "纪录片"),
    (r'\b(pbs|hbo|netflix)\s*(纪录片|documentary)', "纪录片"),

    # ── 体育（优先于电影年份）──
    (r'\b(ufc|mma|boxing|wrestling|nba|nfl|nhl|mlb|f1\b|formula\s*one|motogp|premier\s*league|laliga|serie\s*a)\b', "其他"),

    # ── 教程/学习 ──
    (r'\b(tutorial|course|udemy|lynda|pluralsight|coursera|教学|教程|培训)\b', "其他"),

    # ── 动漫（在所有电视剧规则之前，含剧集+动漫源组合匹配）──
    (r'动漫|动画|anime\b|ova\b|oad\b|bangumi|番剧|国漫|日漫|剧场版|里番', "动漫"),
    (r'字幕组|[简繁日]体?字幕|[简繁]日双语', "动漫"),
    (r'\b(lolihouse|sakurato|hysub|littlebaka|reinforce|vcb-studio|mabors|ane|subsplease|dmhy|moozzi2|kamigami|jsum)\b', "动漫"),
    # 剧集格式 + 动漫源 → 动漫（防止被电视剧规则抢先匹配）
    (r'\.s\d{2,3}.*\.(?:cr|crunchyroll|b-global|baha|sentai|hidive|funimation|varyg|asw|scy|ember|kaleido|doki|horriblesubs|erai-raws)\b', "动漫"),

    # ── 电视剧/剧集 ──
    (r'\bs\d{1,2}\s*e\d{1,2}\b|\bseason\s*\d{1,2}\b|\bepisode\s*\d{1,2}\b', "电视剧"),
    (r'\.s\d{2}e\d{2}\b|\.\d+x\d{2}\b', "电视剧"),
    (r'第[一二三四五六七八九十百\d]+[季部集话]|全\d+集|共\d+集|更新第?\d+集', "电视剧"),
    (r'\.s\d{2,3}\b', "电视剧"),
    (r'\be\d{1,4}\b', "电视剧"),
    (r'美剧|韩剧|日剧|港剧|国产剧|英美剧|netflix\s*原创|disney\+|tv\s*series', "电视剧"),

    # ── 游戏 ──
    (r'\bgame\b|goty|dlc\b|repack|codex|skidrow|fitgirl|gog\b|steam|epic\s*games', "游戏"),
    (r'破解版|汉化版|免安装|模拟器|rom\b|\bps[345]\b|\bxbox\b|\bnintendo\b|\bswitch\s*版', "游戏"),
    (r'\b(goldberg|tenoke|rune|flt|dodi|elamigos|kaos|razor1911)\b', "游戏"),

    # ── 软件 ──
    (r'setup\.exe|installer|\bcrack\b|keygen|patch\b|serial\s*key|激活|注册机|绿色版|便携版|单文件版', "软件"),
    (r'\b(adobe|autodesk|microsoft|vmware|oracle|photoshop|illustrator|after\s*effects|premiere|windows[\s._]\d+|win[\s._]?\d+)', "软件"),
    (r'\b(v\d+\.\d+(?:\.\d+)?)\s*(x64|x86|multilingual|portable|pre-?activated|repack)\b', "软件"),

    # ── 音乐 ──
    (r'\bflac\b|\bmp3\b|\baac\b|\bwav\b|\bdff\b|\bdsd\b|\bape\b|\balac\b|\bogg\b|\bwma\b', "音乐"),
    (r'专辑|单曲|ost\b|soundtrack|album\b|hires|hi-?res|黑胶|无损|母带', "音乐"),
    (r'\.flac|\.mp3|\.aac|\.wav|\.dff|\.dsf|\.ape|\.alac|\.ogg', "音乐"),

    # ── 电影：显式电影关键词或带发布源标记的电影格式 ──
    (r'电影|movie\b|filme?\b|片源|枪版|tc版|ts版|cam\b|hd-?tc', "电影"),
    (r'\b(19|20)\d{2}\b.*\b(blu-?ray|bdrip|web-?dl|webrip|hdrip|remux|dvdrip)\b', "电影"),

    # ── 图像/壁纸 ──
    (r'壁纸|wallpaper|stock\s*photo|图包|写真', "其他"),
]

VALID_CATEGORIES = ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]


def classify_local(name: str) -> str:
    """使用本地规则分类，按优先级匹配"""
    n = name.lower()
    for pattern, cat in LOCAL_RULES:
        if re.search(pattern, n, re.IGNORECASE):
            return cat
    return "其他"


def make_fallback(name: str, reason: str = "local_fallback") -> dict:
    """生成兜底分类结果"""
    cat = classify_local(name)
    return {
        "category": cat,
        "confidence": "low",
        "reason": reason,
        "save_path": cat,
    }
