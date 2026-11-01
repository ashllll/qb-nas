"""infohash 归一化：同一磁力不同写法只留一条。"""

from __future__ import annotations

import base64

from magnet_harvester.utils.infohash import is_valid_infohash, normalize_infohash

HEX_LOWER = "0123456789abcdef0123456789abcdef01234567"
HEX_UPPER = HEX_LOWER.upper()
BASE32 = base64.b32encode(bytes.fromhex(HEX_LOWER)).decode()


def test_hex_is_normalized_to_uppercase():
    assert normalize_infohash(HEX_LOWER) == HEX_UPPER
    assert normalize_infohash(HEX_UPPER) == HEX_UPPER
    assert normalize_infohash(f"  {HEX_LOWER}  ") == HEX_UPPER


def test_base32_is_decoded_to_hex():
    assert normalize_infohash(BASE32) == HEX_UPPER
    assert normalize_infohash(BASE32.lower()) == HEX_UPPER


def test_magnet_and_urn_forms_are_accepted():
    assert normalize_infohash(f"magnet:?xt=urn:btih:{HEX_LOWER}&dn=Something") == HEX_UPPER
    assert normalize_infohash(f"magnet:?xt=urn:btih:{BASE32}&dn=x") == HEX_UPPER
    assert normalize_infohash(f"urn:btih:{HEX_LOWER}") == HEX_UPPER
    assert normalize_infohash(f"btih:{HEX_LOWER}") == HEX_UPPER


def test_percent_encoded_and_escaped_values_are_accepted():
    assert normalize_infohash(f"magnet%3A%3Fxt%3Durn%3Abtih%3A{HEX_LOWER}") == HEX_UPPER
    assert normalize_infohash(f"&amp;magnet:?xt=urn:btih:{HEX_LOWER}") == HEX_UPPER


def test_unrecognized_values_return_none():
    assert normalize_infohash("") is None
    assert normalize_infohash("   ") is None
    assert normalize_infohash("EEEE") is None
    assert normalize_infohash(None) is None
    assert normalize_infohash("magnet:?dn=no-hash") is None


def test_is_valid_infohash_only_accepts_canonical_form():
    assert is_valid_infohash(HEX_UPPER)
    assert not is_valid_infohash(HEX_LOWER)
    assert not is_valid_infohash(BASE32)
    assert not is_valid_infohash(None)


def test_ambiguous_32_char_hex_like_token_is_rejected():
    """32 位全是 hex 字符的串：按 base32 解会得到一个不存在的 infohash。

    站点把 40 位 hex 截断成 32 位很常见，而 base32 infohash 恰好全由 hex 字符
    组成的概率约 2^-32。宁可判为无法识别，也不能用错误的 hash 入库/去重。
    """
    truncated_hex = "abcdef234567abcdef234567abcdef23"
    assert len(truncated_hex) == 32
    assert normalize_infohash(truncated_hex) is None
    assert normalize_infohash(f"magnet:?xt=urn:btih:{truncated_hex}&dn=x") is None


def test_real_base32_with_non_hex_letters_still_decodes():
    # BASE32 里含 G/N 等非 hex 字符，不会被歧义规则误伤
    assert normalize_infohash(BASE32) == HEX_UPPER
