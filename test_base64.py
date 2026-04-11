#!/usr/bin/env python3
"""测试 Base64 磁力链接解码功能"""
import re
import base64

# 导入我们改进的正则表达式
BASE64_MAGNET_RE = re.compile(
    r'bWJhZ25ldD98YWh0dHA[a-zA-Z0-9+/]{20,200}={0,2}',
    re.IGNORECASE,
)

BASE64_MIN_LENGTH = 20
BASE64_MAX_LENGTH = 300

BASE64_VALID_RE = re.compile(
    r'^[a-zA-Z0-9+/]+={0,2}$',
)

BTIH_PATTERN_RE = re.compile(
    r'btih:([a-fA-F0-9]{32,40})',
    re.IGNORECASE,
)

MAGNET_RE = re.compile(
    r'magnet:\?xt=urn:btih:[a-fA-F0-9]{32,64}(?:[^\s\'"<>&\)]+)?',
    re.IGNORECASE,
)


def test_base64_decode():
    print("🧪 测试 Base64 磁力链接解码")
    print("="*60)
    
    # 测试用例
    test_cases = [
        # (描述, 输入文本, 期望结果)
        ("正常 Base64 编码的磁力链接", 
         base64.b64encode(b'magnet:?xt=urn:btih:ABC1234567890ABCDEFGHIJKLMNOP').decode(),
         True),
        
        ("正常 Base64 编码的 HTTP URL",
         base64.b64encode(b'http://example.com/magnet?xt=urn:btih:DEADBEEF1234567890ABCDEFGHIJKL'),
         True),
        
        ("明文磁力链接",
         'magnet:?xt=urn:btih:1234567890ABCDEFGHIJKLMNOPQRSTU',
         False),  # 这个不会被 Base64 正则匹配
        
        ("短字符串（应被过滤）",
         'bWJhZ25ldA==',
         False),
        
        ("超长字符串（应被过滤）",
         'bWJhZ25ldD98YWh0dHA' + 'a' * 300 + '==',
         False),
        
        ("无效 Base64（包含非法字符）",
         'bWJhZ25ldD98YWh0dHA123!!!',
         False),
        
        ("正常的 Base64 字符串（不应匹配）",
         'SGVsbG8gV29ybGQhIFRoaXMgaXMgYSB0ZXN0IG1lc3NhZ2Uu',
         False),
    ]
    
    passed = 0
    failed = 0
    
    for desc, text, should_match in test_cases:
        print(f"\n📝 测试: {desc}")
        print(f"   输入: {text[:80]}{'...' if len(text) > 80 else ''}")
        
        matches = list(BASE64_MAGNET_RE.finditer(text))
        match_count = len(matches)
        
        if should_match:
            if match_count > 0:
                print(f"   ✅ 通过 - 匹配到 {match_count} 个候选")
                passed += 1
            else:
                print(f"   ❌ 失败 - 应该匹配但没有匹配")
                failed += 1
        else:
            if match_count == 0:
                print(f"   ✅ 通过 - 正确过滤掉不相关的字符串")
                passed += 1
            else:
                print(f"   ❌ 失败 - 不应该匹配但匹配了 {match_count} 个")
                failed += 1
    
    print("\n" + "="*60)
    print(f"📊 测试结果: {passed} 通过, {failed} 失败")
    
    if failed == 0:
        print("🎉 所有测试通过！")
    else:
        print("⚠️  有测试失败，请检查实现")
    
    return failed == 0


def test_decode_logic():
    print("\n\n🔬 测试解码逻辑")
    print("="*60)
    
    test_cases = [
        # (描述, Base64 字符串, 期望提取到磁力链接)
        ("标准磁力链接",
         base64.b64encode(b'magnet:?xt=urn:btih:ABC1234567890ABCDEFGHIJKLMNOPQR').decode(),
         True),
        
        ("包含 btih 的文本",
         base64.b64encode(b'Some text with btih:1234567890abcdef1234567890abcdef around').decode(),
         True),
        
        ("普通文本（无磁力）",
         base64.b64encode(b'Hello, this is just a regular text message.').decode(),
         False),
    ]
    
    for desc, b64_str, should_extract in test_cases:
        print(f"\n📝 测试: {desc}")
        print(f"   Base64: {b64_str}")
        
        try:
            if BASE64_VALID_RE.match(b64_str):
                decoded = base64.b64decode(b64_str).decode('utf-8', errors='ignore')
                print(f"   解码: {decoded[:80]}{'...' if len(decoded) > 80 else ''}")
                
                magnets = MAGNET_RE.findall(decoded)
                btih_match = BTIH_PATTERN_RE.search(decoded)
                
                if magnets or btih_match:
                    print(f"   ✅ 找到磁力: {magnets or btih_match.group()}")
                else:
                    print(f"   ℹ️  未找到磁力")
                    
        except Exception as e:
            print(f"   ❌ 解码失败: {e}")


if __name__ == "__main__":
    success = test_base64_decode()
    test_decode_logic()
    
    print("\n" + "="*60)
    print("🎯 测试完成！")
    print("="*60)
    
    exit(0 if success else 1)
