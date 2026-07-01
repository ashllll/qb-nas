"""
MagnetParser — 磁力链接解析与提取工具

从文本中提取磁力链接，支持：
- 标准 magnet:?xt=urn:btih:... 格式
- Base64 编码的磁力链接（`bWFnbmV0...` 开头）
- JSON 字符串中的磁力链接
- HTML/Markdown 文本中的磁力链接
"""

from __future__ import annotations

import base64
import binascii
import html
import logging
import re
import urllib.parse
from collections.abc import Iterable
from typing import Dict, List, Optional, Set

log = logging.getLogger(__name__)

# ── 正则表达式 ──────────────────────────────

BTIH_VALUE_PATTERN = r"(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})"

MAGNET_RE = re.compile(
    rf'magnet:\?xt=urn:btih:{BTIH_VALUE_PATTERN}(?![a-zA-Z0-9])(?:[^\s\'"<>\)]+)?',
    re.IGNORECASE,
)

MAGNET_FULL_RE = re.compile(
    r'magnet:\?([^"\']+)',
    re.IGNORECASE,
)

HASH_RE = re.compile(rf"btih:({BTIH_VALUE_PATTERN})(?![a-zA-Z0-9])", re.IGNORECASE)

SIZE_RE = re.compile(r"(?:xl|size)=(\d+)", re.IGNORECASE)

BASE64_MAGNET_RE = re.compile(
    r"bWFnbmV0[a-zA-Z0-9+/]{10,250}={0,2}",
    re.IGNORECASE,
)

JSON_MAGNET_RE = re.compile(
    r'"(magnet[^\"]+)"|\'(magnet[^\']+)\'',
    re.IGNORECASE,
)

BTIH_PATTERN_RE = re.compile(
    rf"btih:({BTIH_VALUE_PATTERN})(?![a-zA-Z0-9])",
    re.IGNORECASE,
)

BASE64_MIN_LENGTH = 20
BASE64_MAX_LENGTH = 300

BASE64_VALID_RE = re.compile(r"^[a-zA-Z0-9+/]+={0,2}$")


# ── 核心解析函数 ──────────────────────────


def parse_magnet(raw_raw: str) -> Optional[dict]:
    """将单个磁力链接字符串解析为结构化数据"""
    raw = _clean_raw_magnet(raw_raw)
    decoded = urllib.parse.unquote(raw)
    m = HASH_RE.search(decoded)
    if not m:
        return None
    btih = m.group(1).upper()

    dn_match = re.search(r"[?&]dn=([^&]+)", decoded)
    name = urllib.parse.unquote_plus(dn_match.group(1)) if dn_match else f"Unknown_{btih[:8]}"

    xl_match = SIZE_RE.search(decoded)
    size = xl_match.group(1) if xl_match else None

    return {
        "hash": btih,
        "name": name,
        "magnet": raw,  # keep original encoding
        "size": size,
    }


def _clean_raw_magnet(raw: str) -> str:
    """Normalize one candidate without treating spaces inside ``dn`` as delimiters."""
    return html.unescape(raw.strip()).strip("'\" \t\r\n")


def _iter_base64_candidates(text: str):
    """按出现顺序扫描可能的 Base64 编码候选字符串。"""
    seen: Set[str] = set()
    for match in BASE64_MAGNET_RE.finditer(text):
        candidate = match.group()
        if BASE64_MIN_LENGTH <= len(candidate) <= BASE64_MAX_LENGTH and candidate not in seen:
            seen.add(candidate)
            yield candidate


def _decode_candidate(candidate: str) -> str | None:
    """尝试解码单个 Base64 候选，返回其中的磁力链接或 None。"""
    if not BASE64_VALID_RE.match(candidate):
        return None
    try:
        # 补全 Base64 填充，避免缺少 '=' 导致 binascii.Error
        missing_padding = len(candidate) % 4
        if missing_padding:
            candidate += "=" * (4 - missing_padding)
        decoded_bytes = base64.b64decode(candidate)
    except (binascii.Error, ValueError):
        log.debug("Base64 解码失败 (非磁力内容)")
        return None
    decoded = _decode_base64_text(decoded_bytes)
    if not decoded or len(decoded) < 10:
        return None
    return _magnet_from_decoded_text(decoded)


def _decode_base64_text(decoded_bytes: bytes) -> str:
    try:
        return decoded_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        log.debug("Base64 内容包含非法 UTF-8 字节，使用替换符保留位置: %s", e)
        return decoded_bytes.decode("utf-8", errors="replace")


def _magnet_from_decoded_text(decoded: str) -> str | None:
    decoded_lower = decoded.lower()
    if "magnet:" not in decoded_lower and "btih:" not in decoded_lower:
        return None
    magnets = MAGNET_RE.findall(decoded)
    if magnets:
        return magnets[0]
    hash_match = BTIH_PATTERN_RE.search(decoded)
    if hash_match:
        return f"magnet:?xt=urn:btih:{hash_match.group(1).upper()}"
    return None


def try_decode_base64(text: str) -> List[str]:
    """尝试从文本中解码 Base64 编码的磁力链接。"""
    results: List[str] = []
    seen: Set[str] = set()
    for candidate in _iter_base64_candidates(text):
        decoded = _decode_candidate(candidate)
        if decoded and decoded not in seen:
            seen.add(decoded)
            results.append(decoded)
    return results


def extract_magnet_params(raw: str) -> Dict[str, str]:
    """提取磁力链接中的参数"""
    params: Dict[str, str] = {}
    match = MAGNET_FULL_RE.search(raw)
    if match:
        query_string = match.group(1)
        for pair in query_string.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[urllib.parse.unquote_plus(key)] = urllib.parse.unquote_plus(value)
    return params


def deduplicate_magnets(items: List[dict]) -> List[dict]:
    """按 hash 去重"""
    seen: Set[str] = set()
    result: List[dict] = []
    for item in items:
        h = item.get("hash")
        if h is None:
            continue
        if h not in seen:
            seen.add(h)
            result.append(item)
    return result


def extract_from_text(text: str) -> List[dict]:
    """从文本中提取所有磁力链接（主入口函数）

    支持三种模式：
    1. 标准 magnet: 格式
    2. Base64 编码的磁力链接
    3. JSON/属性中的磁力链接（用引号包裹）
    """
    items: List[dict] = []
    seen: Set[str] = set()
    text_sources = _normalise_text_sources(text)

    for raw in _iter_magnet_candidates(text_sources):
        _append_unique_magnet(items, seen, raw)

    return items


def _iter_magnet_candidates(text_sources: Iterable[str]):
    yield from _iter_standard_magnets(text_sources)
    yield from _iter_base64_magnets(text_sources)
    yield from _iter_json_magnets(text_sources)


def _iter_standard_magnets(text_sources: Iterable[str]):
    for source in text_sources:
        yield from MAGNET_RE.findall(source)


def _iter_base64_magnets(text_sources: Iterable[str]):
    for source in text_sources:
        yield from try_decode_base64(source)


def _iter_json_magnets(text_sources: Iterable[str]):
    for source in text_sources:
        for match in JSON_MAGNET_RE.finditer(source):
            raw = match.group(1) or match.group(2)
            if raw:
                yield raw


def _append_unique_magnet(items: List[dict], seen: Set[str], raw: str) -> None:
    item = parse_magnet(raw)
    if item and item["hash"] not in seen:
        seen.add(item["hash"])
        items.append(item)


def _normalise_text_sources(text: str) -> List[str]:
    sources: List[str] = []
    for candidate in (
        text,
        html.unescape(text),
        urllib.parse.unquote(html.unescape(text)),
    ):
        if candidate and candidate not in sources:
            sources.append(candidate)
    return sources
