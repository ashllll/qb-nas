"""
StudioRecognizer — 从标题中提取工作室/厂牌名作为分类。

支持的标题格式:
  StudioName 24 05 20 PerformerNames ...
  StudioName.24.05.20.PerformerNames ...
  [Tag] StudioName 24 05 20 PerformerNames ...
  X-Art 24 01 10 Title XXX 2160p ...
"""
from __future__ import annotations

import re
from typing import Optional

# 已知工作室 → 规范分类名映射
KNOWN_STUDIOS: dict[str, str] = {
    "sexart": "SexArt",
    "metart": "MetArt",
    "xart": "X-Art",
    "x-art": "X-Art",
    "vixen": "Vixen",
    "blacked": "Blacked",
    "tushy": "Tushy",
    "deeper": "Deeper",
    "brazzers": "Brazzers",
    "naughtyamerica": "NaughtyAmerica",
    "realitykings": "RealityKings",
    "bangbros": "BangBros",
    "teamskeet": "TeamSkeet",
    "21sextury": "21Sextury",
    "digitalplayground": "DigitalPlayground",
    "elegantangel": "ElegantAngel",
    "evilangel": "EvilAngel",
    "julesjordan": "JulesJordan",
    "wicked": "Wicked",
    "babes": "Babes",
    "private": "Private",
    "dorcel": "Dorcel",
    "marcdorcel": "MarcDorcel",
    "legalporno": "LegalPorno",
    "joymii": "Joymii",
    "wowgirls": "WowGirls",
    "ultrafilms": "UltraFilms",
    "nubilefilms": "NubileFilms",
    "vivthomas": "VivThomas",
    "hegre": "Hegre",
    "femjoy": "Femjoy",
    "metflix": "Metflix",
}

## 匹配模式：
## 只有 "StudioName + 日期 (YY MM DD)" 格式才触发
## 例如: SexArt 24 05 20  /  SexArt.24.05.20.  /  X-Art 24-05-20
_STUDIO_WITH_DATE = re.compile(
    r"(?:^\[[^\]]+\]\s*)?"                       ## 可选: [Tag] 前缀
    r"([A-Za-z0-9]+(?:[\s.\-][A-Za-z0-9]+)*?)"   ## 工作室名 (含连字符/点)
    r"[\s.]"                                      ## 分隔符
    r"\b\d{2}[\s.\-]\d{2}[\s.\-]\d{2}\b"         ## 日期 YY MM DD（\b 防止匹配 2022.2160p 中的 20.22.21）
    r"(?:[\s.]|$)",                               ## 日期后边界
    re.IGNORECASE,
)


def extract_studio(name: str) -> Optional[str]:
    """从文件名提取工作室名称。"""
    # 清理常见噪音前缀
    cleaned = name.strip()

    # 尝试日期模式匹配
    m = _STUDIO_WITH_DATE.search(cleaned)
    if m:
        raw = m.group(1).strip().rstrip(".")
        normalized = normalize_known_studio(raw)
        if normalized:
            return normalized

    return None


def normalize_known_studio(raw: str) -> Optional[str]:
    key = raw.lower().replace(" ", "").replace(".", "").replace("-", "")
    return KNOWN_STUDIOS.get(key)


def normalize_studio(raw: str) -> str:
    """标准化工作室名称为分类友好的格式。"""
    # 清理首尾标点
    raw = raw.strip(" .-_[]()")
    if not raw:
        return raw

    key = raw.lower().replace(" ", "").replace(".", "").replace("-", "")
    if key in KNOWN_STUDIOS:
        return KNOWN_STUDIOS[key]

    # 智能大小写：已知单词保持
    result = raw[0].upper() + raw[1:] if len(raw) > 1 else raw.upper()
    return result


def recognize(name: str) -> Optional[dict]:
    """识别工作室并返回分类信息。"""
    studio = extract_studio(name)
    if not studio:
        return None
    return {
        "category": studio,
        "confidence": "high",
        "reason": "studio_rule",
        "save_path": studio,
    }
