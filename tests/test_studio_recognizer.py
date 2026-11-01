"""
测试厂牌识别：已知厂牌规范化 + 未知厂牌开放识别（含误匹配防护）。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.studio_recognizer import extract_studio, recognize


# ── 已知厂牌：返回规范名 ──


def test_known_studio_normalized():
    assert extract_studio("SexArt.24.05.20.Performer.Name.2160p") == "SexArt"


def test_known_studio_with_hyphen_variants():
    assert extract_studio("X-Art 24 01 10 Title XXX 2160p") == "X-Art"
    assert extract_studio("xart.24.01.10.Title") == "X-Art"


def test_known_studio_multi_word():
    assert extract_studio("Marc Dorcel 26 04 06 Some Title") == "MarcDorcel"


def test_known_studio_with_tag_prefix():
    assert extract_studio("[XXX] Vixen 25 01 01 Some Title 2160p") == "Vixen"


# ── 未知厂牌：开放识别，避免白名单覆盖不全导致同类内容分类不一致 ──


def test_unknown_studio_recognized_with_original_case():
    assert extract_studio("DorcelClub 26 04 06 Nata Gold XXX 2160p MP4-NBQ") == "DorcelClub"


def test_unknown_studio_preserves_inner_capitals():
    assert (
        extract_studio("StepSiblingsCaught 26 05 14 Nata Gold XXX 2160p MP4")
        == "StepSiblingsCaught"
    )


def test_unknown_studio_lowercase_gets_capitalized():
    assert extract_studio("somestudio 26 01 01 Title") == "Somestudio"


def test_recognize_returns_unknown_studio_as_category():
    result = recognize("DorcelClub 26 04 06 Nata Gold XXX 2160p")
    assert result is not None
    assert result["category"] == "DorcelClub"
    assert result["save_path"] == "DorcelClub"
    assert result["reason"] == "studio_rule"


# ── 误匹配防护：纯数字 / 过短前缀 / 无日期 ──


def test_numeric_prefix_rejected():
    assert extract_studio("1917 19 12 25 Movie Title") is None


def test_short_prefix_rejected():
    assert extract_studio("ab 26 01 01 Title") is None


def test_no_date_pattern_no_match():
    assert extract_studio("Random.Movie.2160p") is None
    assert extract_studio("") is None
    assert extract_studio("DorcelClub Nata Gold XXX 2160p") is None


# ── 误匹配防护:日期后紧跟发布标记(2160p/4k/REPACK 等)不得触发厂牌识别 ──


def test_release_marker_after_date_not_treated_as_studio():
    """Avatar.24.05.20.2160p 是普通片名+两位发行日期,不得误判为厂牌 Avatar。"""
    assert extract_studio("Avatar.24.05.20.2160p") is None
    assert extract_studio("Some.Title.24.05.20.2160p") is None


def test_release_marker_with_dash_separator_not_treated_as_studio():
    assert extract_studio("movie.24-05-20.REPACK") is None


def test_spaced_marker_after_date_not_treated_as_studio():
    assert extract_studio("abc xyz 24 05 20 2160p") is None


def test_legitimate_yy_formats_still_match():
    """合法两位年份格式必须保留(回归保护)。"""
    assert extract_studio("SexArt 24 05 20 Performer Names") == "SexArt"
    assert extract_studio("X-Art 24-05-20 Title 2160p") == "X-Art"
    # 未知厂牌 + 日期后跟内容词(非标记)仍应开放识别
    assert extract_studio("DorcelClub 26 04 06 Nata Gold XXX 2160p MP4-NBQ") == "DorcelClub"


if __name__ == "__main__":
    test_known_studio_normalized()
    test_known_studio_with_hyphen_variants()
    test_known_studio_multi_word()
    test_known_studio_with_tag_prefix()
    test_unknown_studio_recognized_with_original_case()
    test_unknown_studio_preserves_inner_capitals()
    test_unknown_studio_lowercase_gets_capitalized()
    test_recognize_returns_unknown_studio_as_category()
    test_numeric_prefix_rejected()
    test_short_prefix_rejected()
    test_no_date_pattern_no_match()
    print("=== studio recognizer tests passed! ===")
