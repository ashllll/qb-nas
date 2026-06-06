#!/usr/bin/env python3
import base64
import re

print("="*70)
print("测试 Base64 正则表达式的严格性")
print("="*70)

# 当前正则（宽松）
current_re = re.compile(r'(?:bWFnbmV0|aHR0c)[a-zA-Z0-9+/]{10,250}={0,2}', re.IGNORECASE)

# 用户建议的正则（更严格）
suggested_re = re.compile(r'(?:bWFnbmV0|aHR0cHM6Ly9tYWduZXQ)[a-zA-Z0-9+/]{20,200}={0,2}', re.IGNORECASE)

# 更好的折中方案：匹配 "magnet" 或包含 magnet 的 URL
# magnet = bWFnbmV0
# http://magnet = aHR0cDovL21hZ25ldA
# https://magnet = aHR0cHM6Ly9tYWduZXQ
better_re = re.compile(r'(?:bWFnbmV0|aHR0c(?:HM6Ly9tYWduZXQ|DovL21hZ25ldCk))[a-zA-Z0-9+/]{5,200}={0,2}', re.IGNORECASE)

test_cases = [
    ("magnet 开头", "magnet:?xt=urn:btih:ABC1234567890", True),
    ("http magnet URL", "http://example.com/magnet", True),
    ("https magnet URL", "https://magnet.example.com", True),
    ("普通 HTTP URL", "http://example.com/page", False),
    ("普通 HTTPS URL", "https://google.com", False),
    ("短文本", "Hello", False),
]

print("\n测试用例:")
print("-"*70)

results = []

for desc, text, should_match_magnet in test_cases:
    b64 = base64.b64encode(text.encode()).decode()
    
    current = current_re.findall(b64)
    suggested = suggested_re.findall(b64)
    better = better_re.findall(b64)
    
    print(f"\n{desc}:")
    print(f"  原文: {text}")
    print(f"  Base64: {b64}")
    
    current_ok = bool(current) == should_match_magnet
    suggested_ok = bool(suggested) == should_match_magnet
    better_ok = bool(better) == should_match_magnet
    
    print(f"  当前正则:   {'✅' if current_ok else '❌'} {'匹配' if current else '不匹配'}")
    print(f"  建议正则:   {'✅' if suggested_ok else '❌'} {'匹配' if suggested else '不匹配'}")
    print(f"  改进正则:   {'✅' if better_ok else '❌'} {'匹配' if better else '不匹配'}")
    
    results.append((desc, current_ok, suggested_ok, better_ok))

print("\n" + "="*70)
print("统计:")
current_pass = sum(1 for _, c, _, _ in results if c)
suggested_pass = sum(1 for _, _, s, _ in results if s)
better_pass = sum(1 for _, _, _, b in results if b)

print(f"  当前正则: {current_pass}/{len(results)} 通过")
print(f"  建议正则: {suggested_pass}/{len(results)} 通过")
print(f"  改进正则: {better_pass}/{len(results)} 通过")

if better_pass == len(results):
    print("\n✅ 改进正则通过了所有测试！")
elif suggested_pass > current_pass:
    print("\n💡 建议正则更严格，可以考虑使用")

print("="*70)
