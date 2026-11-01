"""Infohash 归一化 — 同一个种子只有一个键。

用户/站点给出的 btih 有多种写法：大写或小写 hex、32 位 base32、URL 编码、
带 ``magnet:?xt=urn:btih:`` 前缀或裸哈希。资源库以 infohash 为主键，
所以每个入口（Mac 上报、页面抓取、手动粘贴）都必须归一到同一形式：

    40 位大写 hex

base32 一律解码成 hex；其余写法只做大小写归一，不改变信息。
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import urllib.parse

_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_CANONICAL_RE = re.compile(r"^[0-9A-F]{40}$")
_BASE32_RE = re.compile(r"^[A-Za-z2-7]{32}$")
_BTIH_RE = re.compile(r"btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})", re.IGNORECASE)


def normalize_infohash(value: str | None) -> str | None:
    """把任意 btih 写法归一成 40 位大写 hex；无法识别返回 None。"""
    if not value or not isinstance(value, str):
        return None
    for candidate in _candidates(value):
        normalized = _normalize_token(candidate)
        if normalized is not None:
            return normalized
    return None


def is_valid_infohash(value: str | None) -> bool:
    """判断一个值是否已经是归一化后的 40 位 hex。"""
    return bool(value) and bool(_CANONICAL_RE.match(value or ""))


def _candidates(value: str):
    """按「先 BTIH 取值，再整体」的顺序产出候选 token。"""
    text = html.unescape(value.strip()).strip("'\" \t\r\n")
    if not text:
        return
    decoded = urllib.parse.unquote(text)
    for source in (text, decoded):
        match = _BTIH_RE.search(source)
        if match:
            yield match.group(1)
    for source in (text, decoded):
        if source.lower().startswith("magnet:"):
            continue  # 整串磁力链接本身不是哈希
        token = source.strip()
        if token:
            yield token


def _normalize_token(token: str) -> str | None:
    if _HEX40_RE.match(token):
        return token.upper()
    if _BASE32_RE.match(token):
        return _base32_to_hex(token)
    return None


def _base32_to_hex(token: str) -> str | None:
    try:
        raw = base64.b32decode(token.upper())
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 20:
        return None
    return raw.hex().upper()
