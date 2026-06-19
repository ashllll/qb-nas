"""
P1-10: 哈希校验统一测试

缺陷: HASH_RE 使用 {32,64} 匹配哈希长度，但 BTIH_PATTERN_RE 使用 {32,40}
      SHA-1 哈希应为 40 字符，{32,64} 太宽泛可能匹配无效哈希
修复: 统一为 {32,40}
"""

from magnet_harvester.magnet_parser import HASH_RE, MAGNET_RE, BTIH_PATTERN_RE, parse_magnet


def test_hash_re_rejects_64_char_hash():
    """HASH_RE 不应匹配 64 字符的无效哈希"""
    hash_64 = "a" * 64
    text = f"magnet:?xt=urn:btih:{hash_64}"
    assert not HASH_RE.search(text), "64 字符哈希应被拒绝"


def test_hash_re_accepts_40_char_hash():
    """HASH_RE 应匹配 40 字符的标准 SHA-1 哈希"""
    hash_40 = "a" * 40
    text = f"magnet:?xt=urn:btih:{hash_40}"
    match = HASH_RE.search(text)
    assert match is not None
    assert match.group(1) == hash_40


def test_magnet_re_rejects_64_char_hash():
    """MAGNET_RE 不应匹配 64 字符的无效磁力链接"""
    hash_64 = "a" * 64
    text = f"magnet:?xt=urn:btih:{hash_64}"
    assert not MAGNET_RE.search(text), "64 字符磁力链接应被拒绝"


def test_magnet_re_accepts_40_char_hash():
    """MAGNET_RE 应匹配 40 字符的标准磁力链接"""
    hash_40 = "a" * 40
    text = f"magnet:?xt=urn:btih:{hash_40}&dn=test"
    match = MAGNET_RE.search(text)
    assert match is not None


def test_btih_pattern_re_consistent_with_hash_re():
    """BTIH_PATTERN_RE 和 HASH_RE 应有相同的哈希长度限制"""
    hash_40 = "a" * 40
    hash_64 = "a" * 64

    assert BTIH_PATTERN_RE.search(f"btih:{hash_40}")
    assert not BTIH_PATTERN_RE.search(f"btih:{hash_64}")

    assert HASH_RE.search(f"btih:{hash_40}")
    assert not HASH_RE.search(f"btih:{hash_64}")


def test_parse_magnet_validates_hash_length():
    """parse_magnet 应拒绝无效长度的哈希"""
    hash_64 = "a" * 64
    result = parse_magnet(f"magnet:?xt=urn:btih:{hash_64}")
    assert result is None, "64 字符哈希应被拒绝"

    hash_40 = "a" * 40
    result = parse_magnet(f"magnet:?xt=urn:btih:{hash_40}&dn=test")
    assert result is not None
    assert result["hash"] == hash_40.upper()
