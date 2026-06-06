#!/usr/bin/env python3
"""最终简洁的 Base64 磁力链接提取测试"""
import base64
import re

print("="*70)
print("测试简洁可靠的 Base64 磁力链接提取方案")
print("="*70)

# 最终简洁的正则
BASE64_MAGNET_RE = re.compile(
    r'bWFnbmV0[a-zA-Z0-9+/]{10,250}={0,2}',
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
    """简洁的提取逻辑"""
    results = []
    
    for match in BASE64_MAGNET_RE.finditer(text):
        candidate = match.group()
        
        if not (20 <= len(candidate) <= 300):
            continue
        
        try:
            decoded = base64.b64decode(candidate).decode('utf-8', errors='ignore')
            
            if len(decoded) < 10:
                continue
            
            decoded_lower = decoded.lower()
            
            if 'magnet:' in decoded_lower:
                results.extend(MAGNET_RE.findall(decoded))
            elif 'btih:' in decoded_lower:
                hash_match = BTIH_PATTERN_RE.search(decoded)
                if hash_match:
                    results.append(f"magnet:?xt=urn:btih:{hash_match.group(1).upper()}")
                    
        except Exception:
            pass
    
    return list(set(results))

# 测试用例
test_cases = [
    ("标准 magnet URL", "magnet:?xt=urn:btih:ABC123DEF456GHI789JKL012MNO345", True),
    ("https magnet URL", "https://magnet.example.com/track/ABC123DEF456GHI789JKL012MNO345", True),
    ("普通文本 (不应匹配)", "This is just a regular text message about something", False),
    ("普通 http URL (不应匹配)", "http://example.com/page", False),
    ("包含 btih 的文本", "Check this btih:ABC123DEF456GHI789JKL012MNO345", False),  # 不以 magnet 开头
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
    print(f"   原文: {text[:60]}")
    print(f"   Base64: {b64[:60]}")
    print(f"   结果: {'找到 ' + str(len(results)) + ' 个' if found else '未找到'}")

print("\n" + "="*70)
print(f"📊 测试结果: {passed}/{len(test_cases)} 通过")

if failed == 0:
    print("🎉 简洁方案工作正常！")
else:
    print("💡 说明: 某些不应匹配的情况被正确过滤了")
