#!/usr/bin/env python3
"""测试 Base64 异常处理"""
import base64
import binascii
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)s - %(name)s - %(message)s'
)

log = logging.getLogger(__name__)

def test_base64_decode_with_exception_handling():
    """测试 Base64 解码的异常处理"""
    
    test_cases = [
        # (描述, 输入, 期望异常)
        ("有效 Base64 - magnet URL", 
         "bWFnbmV0Oj94dD11cm46YnRpaDpBQkMxMjM0NTY3ODkwQUJDREVGR0hJSktMTk9Q", 
         None),
        
        ("无效 Base64 - 错误填充",
         "invalid_base64_with_bad_padding==",
         binascii.Error),
        
        ("无效 Base64 - 非法字符",
         "Invalid!!!Base64!!!String!!!",
         (binascii.Error, ValueError)),
        
        ("有效的 Base64 但非 UTF-8",
         None,  # 我们会创建一个包含非 UTF-8 字节的字符串
         None),  # 因为我们使用 errors='ignore'，所以不会抛出异常
        
        ("空字符串",
         "",
         None),
    ]
    
    print("="*70)
    print("测试 Base64 解码异常处理")
    print("="*70)
    
    for desc, test_input, expected_exception in test_cases:
        print(f"\n测试: {desc}")
        print(f"输入: {test_input}")
        
        if test_input is None:
            # 创建一个包含非 UTF-8 字节的字符串
            test_input = bytes([0x80, 0x81, 0x82]).hex()
            test_input = base64.b64encode(test_input.encode()).decode()
            print(f"实际输入: {test_input}")
        
        try:
            if not test_input:
                decoded = ""
            else:
                # 分步解码以测试各个异常
                decoded_bytes = base64.b64decode(test_input)
                decoded = decoded_bytes.decode('utf-8', errors='ignore')
            
            print(f"✅ 解码成功: {decoded[:50]}{'...' if len(decoded) > 50 else ''}")
            
            if expected_exception is None:
                print("   ✅ 符合预期（无异常）")
            else:
                print(f"   ❌ 不符合预期（应该抛出异常 {expected_exception}）")
                
        except binascii.Error as e:
            print(f"✅ 捕获 binascii.Error: {e}")
            if expected_exception == binascii.Error or (isinstance(expected_exception, tuple) and binascii.Error in expected_exception):
                print("   ✅ 符合预期")
            else:
                print(f"   ❌ 不符合预期（期望 {expected_exception}）")
                
        except ValueError as e:
            print(f"✅ 捕获 ValueError: {e}")
            if expected_exception == ValueError or (isinstance(expected_exception, tuple) and ValueError in expected_exception):
                print("   ✅ 符合预期")
            else:
                print(f"   ❌ 不符合预期（期望 {expected_exception}）")
                
        except UnicodeDecodeError as e:
            print(f"✅ 捕获 UnicodeDecodeError: {e}")
            if expected_exception == UnicodeDecodeError:
                print("   ✅ 符合预期")
            else:
                print(f"   ❌ 不符合预期（期望 {expected_exception}）")
                
        except Exception as e:
            print(f"❌ 捕获其他异常: {type(e).__name__}: {e}")
            if expected_exception == Exception or (isinstance(expected_exception, tuple) and Exception in expected_exception):
                print("   ✅ 符合预期")
            else:
                print(f"   ❌ 不符合预期（期望 {expected_exception}）")
    
    print("\n" + "="*70)
    print("✅ 异常处理测试完成")
    print("="*70)

if __name__ == "__main__":
    test_base64_decode_with_exception_handling()
