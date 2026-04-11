#!/usr/bin/env python3
import base64
import re

print("测试修复后的 Base64 正则表达式")
print("="*60)

# 修复后的正则
BASE64_MAGNET_RE = re.compile(
    r'(?:bWFnbmV0|aHR0c)[a-zA-Z0-9+/]{10,250}={0,2}',
    re.IGNORECASE,
)

test_cases = [
    ("正常磁力链接 Base64", base64.b64encode(b'magnet:?xt=urn:btih:ABC1234567890ABCDEFGHIJKLMNOP').decode(), True),
    ("HTTP URL Base64", base64.b64encode(b'http://example.com/magnet').decode(), True),
    ("普通文本 Base64（不应匹配）", base64.b64encode(b'Hello World, this is a test message').decode(), False),
    ("短 Base64（不应匹配）", 'bWFnbmV0', False),
]

passed = 0
failed = 0

for desc, text, should_match in test_cases:
    print(f"\n{desc}:")
    print(f"  Base64: {text}")
    print(f"  长度: {len(text)}")
    
    # 使用 finditer 而不是 findall
    matches = list(BASE64_MAGNET_RE.finditer(text))
    
    if should_match:
        if matches:
            print(f"  ✅ 正确匹配: {matches[0].group()}")
            passed += 1
        else:
            print(f"  ❌ 应该匹配但没有匹配")
            failed += 1
    else:
        if not matches:
            print(f"  ✅ 正确过滤")
            passed += 1
        else:
            print(f"  ❌ 不应该匹配但匹配了: {matches[0].group()}")
            failed += 1

print("\n" + "="*60)
print(f"测试结果: {passed} 通过, {failed} 失败")
print("🎉 所有测试通过!" if failed == 0 else "⚠️  有测试失败")
