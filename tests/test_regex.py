#!/usr/bin/env python3
import base64
import re

print("测试 Base64 正则表达式")
print("="*60)

# 当前的正则（有问题）
current_re = re.compile(r'bWJhZ25ldD98YWh0dHA[a-zA-Z0-9+/]{20,200}={0,2}', re.IGNORECASE)

# 改进的正则
improved_re = re.compile(r'(?:bWJhZ25ldA|98YWh0dHA)[a-zA-Z0-9+/]{15,180}={0,2}', re.IGNORECASE)

test_cases = [
    ("正常磁力链接 Base64", base64.b64encode(b'magnet:?xt=urn:btih:ABC1234567890ABCDEFGHIJKLMNOP').decode()),
    ("正常 HTTP URL Base64", base64.b64encode(b'http://example.com/magnet').decode()),
    ("普通文本 Base64", base64.b64encode(b'Hello World, this is a test message').decode()),
]

for desc, text in test_cases:
    print(f"\n{desc}:")
    print(f"  Base64: {text}")
    print(f"  长度: {len(text)}")
    
    current = current_re.findall(text)
    improved = improved_re.findall(text)
    
    print(f"  当前正则: {'匹配' if current else '不匹配'}")
    print(f"  改进正则: {'匹配' if improved else '不匹配'}")
