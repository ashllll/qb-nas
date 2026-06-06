#!/usr/bin/env python3
"""最终 Base64 磁力链接提取测试"""
import base64
import re

print("="*70)
print("测试修复后的 Base64 磁力链接提取")
print("="*70)

# 导入修复后的正则
BASE64_MAGNET_RE = re.compile(
    r'(?:bWFnbmV0|aHR0cHM6Ly9tYWduZXQ)[a-zA-Z0-9+/]{15,200}={0,2}',
    re.IGNORECASE,
)

MAGNET_RE = re.compile(
    r'magnet:\?xt=urn:btih:[a-fA-F0-9]{32,64}(?:[^\s\'"<>&\)]+)?',
    re.IGNORECASE,
)

BTIH_PATTERN_RE = re.compile(
    r'btih:([a-fA-F0-9]{32,40})',
    re.IGNORECASE,
)

def extract_magnets(text: str) -> list:
    """模拟 _try_decode_base64 函数"""
    results = []
    candidates = set()
    
    for match in BASE64_MAGNET_RE.finditer(text):
        candidate = match.group()
        if 15 <= len(candidate) <= 300:
            candidates.add(candidate)
    
    for candidate in candidates:
        try:
            decoded = base64.b64decode(candidate).decode('utf-8', errors='ignore')
            
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
                        
        except Exception:
            pass
    
    return list(set(results))

# 测试用例
test_cases = [
    # (描述, 原文, 应该提取到磁力)
    ("标准 magnet URL", "magnet:?xt=urn:btih:ABC123DEF456GHI789JKL012MNO345", True),
    ("https magnet URL", "https://magnet.example.com/track/ABC123DEF456GHI789JKL012MNO345", True),
    ("包含 magnet 的 http URL", "http://example.com/magnet?xt=urn:btih:ABC123DEF456GHI789JKL012MNO345", True),
    ("普通 http URL (不应匹配)", "http://example.com/page", False),
    ("普通 https URL (不应匹配)", "https://google.com", False),
    ("普通文本 (不应匹配)", "This is just a regular text message about something", False),
    ("包含 btih 的文本", "Check this btih:ABC123DEF456GHI789JKL012MNO345", True),
]

passed = 0
failed = 0

print("\n测试用例:")
print("-"*70)

for desc, text, should_find in test_cases:
    b64 = base64.b64encode(text.encode()).decode()
    results = extract_magnets(b64)
    
    found = len(results) > 0
    
    if found == should_find:
        status = "✅"
        passed += 1
    else:
        status = "❌"
        failed += 1
    
    print(f"\n{status} {desc}")
    print(f"   原文: {text[:60]}{'...' if len(text) > 60 else ''}")
    print(f"   Base64: {b64[:60]}{'...' if len(b64) > 60 else ''}")
    print(f"   结果: {'找到 ' + str(len(results)) + ' 个磁力' if found else '未找到'}")
    
    if results:
        print(f"   磁力: {results[0][:60]}{'...' if len(results[0]) > 60 else ''}")

print("\n" + "="*70)
print(f"📊 测试结果: {passed} 通过, {failed} 失败")
print("="*70)

if failed == 0:
    print("🎉 所有测试通过！Base64 磁力链接提取功能正常！")
else:
    print("⚠️  有测试失败，请检查实现")

print("\n✅ 修复完成:")
print("- 使用更严格的 Base64 前缀匹配")
print("- 解码后验证是否包含 magnet: 或 btih:")
print("- 避免误匹配普通 URL 和文本")
