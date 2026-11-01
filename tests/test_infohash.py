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
