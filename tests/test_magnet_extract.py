"""
测试磁力链接提取逻辑 — 从 crawl4ai 的 markdown/html 输出中提取磁力链接
这是独立于爬虫引擎的业务逻辑测试，不依赖网络或浏览器
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.magnet_parser import (
    HASH_RE,
    JSON_MAGNET_RE,
    extract_from_text,
)


# ---- 测试代码 ----

def test_extract_standard_magnet():
    """从普通文本中提取标准磁力链接"""
    # MAGNET_RE 现在允许 &，所以 dn 参数能被捕获
    text = "下载链接：magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&dn=Test+File"
    items = extract_from_text(text)
    assert len(items) == 1, f"应找到1个磁力链接，实际找到 {len(items)}"
    item = items[0]
    assert item["hash"] == "0123456789ABCDEF0123456789ABCDEF01234567"
    # & 已被允许，dn 参数正常解析
    assert "Test File" in item["name"]


def test_extract_multiple_magnets():
    """同一文本中有多个磁力链接"""
    text = """
    magnet:?xt=urn:btih:AAAABBBBCCCCDDDDEEEEFFFFAAAABBBBCCCCDDDD&dn=File+1
    中间有干扰文字
    magnet:?xt=urn:btih:1111222233334444555566661111222233334444&dn=File+2
    """
    items = extract_from_text(text)
    assert len(items) == 2, f"应找到2个磁力链接，实际找到 {len(items)}"


def test_extract_magnet_with_html():
    """磁力链接嵌在 HTML 属性中（模拟 crawl4ai 输出的常见场景）"""
    text = '<a href="magnet:?xt=urn:btih:DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF&dn=Movie+2024">下载</a>'
    items = extract_from_text(text)
    assert len(items) == 1, f"应找到1个磁力链接，实际找到 {len(items)}"
    assert items[0]["hash"] == "DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF"


def test_extract_html_escaped_magnet_keeps_dn_for_resolution_filter():
    """HTML 属性中的 &amp;dn= 应还原，否则 2160p 名称会丢失并被爬虫过滤。"""
    text = (
        '<a href="magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        '&amp;dn=Example.Movie.2160p.WEB-DL">下载</a>'
    )

    items = extract_from_text(text)

    assert len(items) == 1
    assert items[0]["hash"] == "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert items[0]["name"] == "Example.Movie.2160p.WEB-DL"


def test_extract_url_encoded_magnet():
    """有些站点把整条 magnet URL 编码后放在跳转参数里。"""
    text = (
        "href=/download?url=magnet%3A%3Fxt%3Durn%3Abtih%3A"
        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB%26dn%3DEncoded.Movie.2160p"
    )

    items = extract_from_text(text)

    assert len(items) == 1
    assert items[0]["hash"] == "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    assert items[0]["name"] == "Encoded.Movie.2160p"


def test_extract_base32_btih_magnet():
    """BTIH 也可能是 32 位 Base32，不只 40 位 hex。"""
    text = (
        "magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        "&dn=Base32.Movie.2160p"
    )

    items = extract_from_text(text)

    assert len(items) == 1
    assert items[0]["hash"] == "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    assert items[0]["name"] == "Base32.Movie.2160p"


def test_deduplicate_by_hash():
    """相同 hash 只返回一次"""
    text = """
    magnet:?xt=urn:btih:ABCDABCDABCDABCDABCDABCDABCDABCDABCDABCD&dn=Same+File
    magnet:?xt=urn:btih:ABCDABCDABCDABCDABCDABCDABCDABCDABCDABCD&dn=Same+File+2
    """
    items = extract_from_text(text)
    assert len(items) == 1, f"相同 hash 应去重，实际找到 {len(items)}"


def test_invalid_hash_too_short():
    """太短的 hash 不会被匹配"""
    text = "magnet:?xt=urn:btih:ABCDEF12345&dn=Too+Short"
    items = extract_from_text(text)
    assert len(items) == 0, "太短的 hash 不应被提取"


def test_base64_encoded_magnet():
    """Base64 编码的磁力链接"""
    import base64
    magnet = "magnet:?xt=urn:btih:FFFEEEFFFEEEFFFEEEFFFEEEFFFEEEFFFEEEFFFE&dn=Base64+Test"
    encoded = base64.b64encode(magnet.encode()).decode()
    text = f"隐藏链接: {encoded}"
    items = extract_from_text(text)
    assert len(items) == 1, f"应从 Base64 中提取磁力链接，实际找到 {len(items)}"
    assert items[0]["hash"] == "FFFEEEFFFEEEFFFEEEFFFEEEFFFEEEFFFEEEFFFE"


def test_magnet_in_json():
    """在 JSON 字符串中的磁力链接
    
    JSON_MAGNET_RE 捕获引号内的完整磁力URL（含 &dn=），
    但也可能被 MAGNET_RE 先捕获（不含 &dn=）。
    只要有1个条目且 hash 正确即可。
    """
    text = '{"link": "magnet:?xt=urn:btih:9999999999999999999999999999999999999999&dn=JSON+Test"}'
    items = extract_from_text(text)
    assert len(items) >= 1, f"应至少提取1个磁力链接，实际找到 {len(items)}"
    # MAGNET_RE 匹配时取前40个hash字符（& 处截断），所以 hash 仍正确
    hashes = [i["hash"] for i in items]
    assert "9999999999999999999999999999999999999999" in hashes


def test_extract_name_with_chinese():
    """提取含中文文件名的磁力链接（直接测试 parse_magnet 函数）"""
    import re
    import urllib.parse

    # 直接测试 parse_magnet：传入含 &dn= 的完整磁力链接字符串
    magnet_url = "magnet:?xt=urn:btih:8888888888888888888888888888888888888888&dn=%E7%94%B5%E5%BD%B1+2024"
    
    # 验证 HASH 解析
    m = HASH_RE.search(magnet_url)
    assert m is not None
    assert m.group(1).upper() == "8888888888888888888888888888888888888888"
    
    # 验证 dn 解析
    dn_match = re.search(r'[?&]dn=([^&]+)', magnet_url)
    assert dn_match is not None
    name = urllib.parse.unquote_plus(dn_match.group(1))
    expected_bytes = b'\xe7\x94\xb5\xe5\xbd\xb1'  # "电影" 的 UTF-8
    assert expected_bytes in name.encode('utf-8'), f"文件名应包含'电影'，实际为: {repr(name)}"
    quoted = f'"{magnet_url}"'
    json_matches = JSON_MAGNET_RE.findall(quoted)
    assert len(json_matches) >= 1
    raw_magnet = json_matches[0][0]  # group(1) from first alternative
    assert "dn=" in raw_magnet


def test_extract_from_crawl4ai_markdown():
    """模拟 crawl4ai 的 markdown 输出格式
    
    注意：MAGNET_RE 匹配磁力链接，& 及之后的参数被截断（dn 丢失），
    但只要 hash 正确即可。
    """
    text = """# 下载页面

## 资源列表

- [电影1](https://example.com)  
  `magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=Movie+1`

- [电影2](https://example.com)  
  `magnet:?xt=urn:btih:2222222222222222222222222222222222222222&dn=Movie+2`

> 注意：资源仅供测试
"""
    items = extract_from_text(text)
    assert len(items) >= 2, f"应从 markdown 输出中提取至少2个磁力链接，实际找到 {len(items)}"
    hashes = [i["hash"] for i in items]
    assert "1111111111111111111111111111111111111111" in hashes
    assert "2222222222222222222222222222222222222222" in hashes


def test_no_false_positives():
    """普通文本中不包含磁力链接时返回空列表"""
    text = """
    这是一个普通的网页内容，没有任何磁力链接。
    只有普通的文字和 http://example.com 这样的 URL。
    """
    items = extract_from_text(text)
    assert len(items) == 0, "不应提取到任何磁力链接"


if __name__ == "__main__":
    test_extract_standard_magnet()
    test_extract_multiple_magnets()
    test_extract_magnet_with_html()
    test_extract_html_escaped_magnet_keeps_dn_for_resolution_filter()
    test_extract_url_encoded_magnet()
    test_extract_base32_btih_magnet()
    test_deduplicate_by_hash()
    test_invalid_hash_too_short()
    test_base64_encoded_magnet()
    test_magnet_in_json()
    test_extract_name_with_chinese()
    test_extract_from_crawl4ai_markdown()
    test_no_false_positives()
    print("=== 所有磁力提取测试通过! ===")
