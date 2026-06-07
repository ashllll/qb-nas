"""
测试 StudioRecognizer — 从文件名识别成人厂牌
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.studio_recognizer import StudioRecognizer


def test_sexart_recognized():
    """SexArt 厂牌可从文件名头部识别"""
    r = StudioRecognizer()
    result = r.recognize("SexArt.26.02.01.Bonnie.Dolce.In.Your.Hands.XXX.2160p.MP4-WRB")
    assert result is not None
    assert result["name"] == "SexArt"
    assert result["save_path"] == "SexArt"


def test_brazzers_recognized():
    r = StudioRecognizer()
    result = r.recognize("BrazzersExxtra.24.05.16.XXX.1080p.MP4-WRB")
    assert result is not None
    assert result["name"] == "Brazzers"


def test_bangbros_in_brackets():
    """括号中的厂牌也能识别"""
    r = StudioRecognizer()
    result = r.recognize("Big Ass Black Beauties 14 [BangBros 2026] XXX WEB-DL 1080p MP4-P2P")
    assert result is not None
    assert result["name"] == "BangBros"


def test_not_adult_returns_none():
    """普通电影不应被识别为成人厂牌"""
    r = StudioRecognizer()
    result = r.recognize("Avatar.The.Way.of.Water.2022.2160p.BluRay.x264")
    assert result is None


if __name__ == "__main__":
    test_sexart_recognized()
    test_brazzers_recognized()
    test_bangbros_in_brackets()
    test_not_adult_returns_none()
    print("=== StudioRecognizer tests passed! ===")
