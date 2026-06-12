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
import logging
import re
import urllib.parse
from typing import Dict, List, Optional, Set

log = logging.getLogger(__name__)

# ── 正则表达式 ──────────────────────────────

MAGNET_RE = re.compile(
    r'magnet:\?xt=urn:btih:[a-fA-F0-9]{32,40}(?![a-fA-F0-9])(?:[^\s\'"<>\)]+)?',
    re.IGNORECASE,
)

MAGNET_FULL_RE = re.compile(
    r'magnet:\?([^"\']+)',
    re.IGNORECASE,
)

HASH_RE = re.compile(r'btih:([a-fA-F0-9]{32,40})(?![a-fA-F0-9])', re.IGNORECASE)

SIZE_RE = re.compile(r'xl=(?:(\d+)|size=(\d+))', re.IGNORECASE)

BASE64_MAGNET_RE = re.compile(
    r'bWFnbmV0[a-zA-Z0-9+/]{10,250}={0,2}',
    re.IGNORECASE,
)

JSON_MAGNET_RE = re.compile(
    r'"(magnet[^\"]+)"|\'(magnet[^\']+)\'',
    re.IGNORECASE,
)

BTIH_PATTERN_RE = re.compile(
    r'btih:([a-fA-F0-9]{32,40})(?![a-fA-F0-9])',
    re.IGNORECASE,
)

BASE64_MIN_LENGTH = 20
BASE64_MAX_LENGTH = 300

BASE64_VALID_RE = re.compile(r'^[a-zA-Z0-9+/]+={0,2}$')


# ── 核心解析函数 ──────────────────────────


def parse_magnet(raw: str) -> Optional[dict]:
    """将单个磁力链接字符串解析为结构化数据

    返回:
        {
            "hash": "infohash (大写)",
            "name": "文件名 (URL解码后)",
            "magnet": "原始磁力链接",
            "size": "文件大小 (如果存在)",
        }
        或 None (如果无法解析)
    """
    raw = raw.strip().rstrip("'\"").split()[0]
    m = HASH_RE.search(raw)
    if not m:
        return None
    btih = m.group(1).upper()

    dn_match = re.search(r'[?&]dn=([^&]+)', raw)
    name = urllib.parse.unquote_plus(dn_match.group(1)) if dn_match else f"Unknown_{btih[:8]}"

    xl_match = SIZE_RE.search(raw)
    size = xl_match.group(1) or xl_match.group(2) if xl_match else None

    return {
        "hash": btih,
        "name": name,
        "magnet": raw,
        "size": size,
    }


def try_decode_base64(text: str) -> List[str]:
    """尝试从文本中解码 Base64 编码的磁力链接"""
    results: List[str] = []
    candidates: Set[str] = set()

    for match in BASE64_MAGNET_RE.finditer(text):
        candidate = match.group()
        if BASE64_MIN_LENGTH <= len(candidate) <= BASE64_MAX_LENGTH:
            candidates.add(candidate)

    for candidate in candidates:
        try:
            if not BASE64_VALID_RE.match(candidate):
                continue

            decoded_bytes = base64.b64decode(candidate)
            decoded = decoded_bytes.decode('utf-8', errors='ignore')

            if not decoded or len(decoded) < 10:
                continue

            decoded_lower = decoded.lower()
            if 'magnet:' in decoded_lower or 'btih:' in decoded_lower:
                magnets = MAGNET_RE.findall(decoded)
                if magnets:
                    results.extend(magnets)
                else:
                    hash_match = BTIH_PATTERN_RE.search(decoded)
                    if hash_match:
                        magnet = f"magnet:?xt=urn:btih:{hash_match.group(1).upper()}"
                        results.append(magnet)

        except (binascii.Error, ValueError, UnicodeDecodeError):
            log.debug(f"Base64 解码失败 (非磁力内容)")
        except Exception:
            log.debug(f"Base64 解码未知错误")

    return list(set(results))


def extract_magnet_params(raw: str) -> Dict[str, str]:
    """提取磁力链接中的参数"""
    params: Dict[str, str] = {}
    match = MAGNET_FULL_RE.search(raw)
    if match:
        query_string = match.group(1)
        for pair in query_string.split('&'):
            if '=' in pair:
                key, value = pair.split('=', 1)
                params[urllib.parse.unquote_plus(key)] = urllib.parse.unquote_plus(value)
    return params


def deduplicate_magnets(items: List[dict]) -> List[dict]:
    """按 hash 去重"""
    seen: Set[str] = set()
    result: List[dict] = []
    for item in items:
        if item["hash"] not in seen:
            seen.add(item["hash"])
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

    # 模式1：标准 magnet: 链接
    for raw in MAGNET_RE.findall(text):
        item = parse_magnet(raw)
        if item and item["hash"] not in seen:
            seen.add(item["hash"])
            items.append(item)

    # 模式2：Base64 编码
    decoded_b64 = try_decode_base64(text)
    for raw in decoded_b64:
        item = parse_magnet(raw)
        if item and item["hash"] not in seen:
            seen.add(item["hash"])
            items.append(item)

    # 模式3：JSON 引号中的磁力链接
    for m in JSON_MAGNET_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if raw:
            item = parse_magnet(raw)
            if item and item["hash"] not in seen:
                seen.add(item["hash"])
                items.append(item)

    return items
