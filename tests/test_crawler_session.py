"""
TDD 循环 3: 爬虫并发控制与生命周期管理
验证会话隔离和安全取消（通过 AST 分析，不依赖 crawl4ai）
"""
import ast
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _get_crawler_source() -> str:
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(repo_root, "magnet_harvester", "crawler.py"), "r", encoding="utf-8") as f:
        return f.read()


def _find_method_source(source: str, method_name: str) -> str:
    """从类源码中提取方法源码（简化版，基于 AST）"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MagnetCrawler":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    start = item.lineno - 1
                    end = item.end_lineno
                    lines = source.splitlines()
                    return "\n".join(lines[start:end])
    return ""


# ═══════════════════════════════════════════════════
# 示踪弹: _global_seen 应为会话级（非实例级）
# ═══════════════════════════════════════════════════

def test_crawler_has_no_instance_level_seen_set():
    """MagnetCrawler 不应在实例级别维护 _global_seen"""
    source = _get_crawler_source()
    # __init__ 中不应创建 _global_seen
    init_source = _find_method_source(source, "__init__")
    assert "_global_seen" not in init_source, "__init__ 中不应有 _global_seen"


# ═══════════════════════════════════════════════════
# 增量测试 2: crawl() 使用 crawl4ai 原生批量流
# ═══════════════════════════════════════════════════

def test_crawler_uses_arun_many_not_manual_worker_pool():
    """_fetch_many_stream 应使用 crawl4ai arun_many，而不是手写 worker 池。"""
    source = _get_crawler_source()
    session_source = _find_method_source(source, "_run_crawl_session")
    fetch_many_source = _find_method_source(source, "_fetch_many_stream")
    assert "TaskGroup" not in session_source, "不应使用 TaskGroup"
    assert "create_task" not in session_source, "不应在会话层手写 worker task"
    assert "arun_many" in fetch_many_source, "_fetch_many_stream 应使用 crawl4ai arun_many"


# ═══════════════════════════════════════════════════
# 增量测试 3: seen 集合通过参数传递（会话隔离）
# ═══════════════════════════════════════════════════

def test_seen_set_passed_as_parameter():
    """seen 集合应通过参数链传递，而非实例属性"""
    source = _get_crawler_source()

    # crawl() 中应创建局部 seen 集合
    crawl_source = _find_method_source(source, "crawl")
    assert "seen: Set[str] = set()" in crawl_source, "crawl() 中应创建局部 seen"

    # _run_crawl_session 应接收 seen 参数
    session_source = _find_method_source(source, "_run_crawl_session")
    assert "seen: Set[str]" in session_source, "_run_crawl_session 应接收 seen"

    # _handle_crawl_result 应使用 seen 参数（而非 self._global_seen）
    result_source = _find_method_source(source, "_handle_crawl_result")
    assert "self._global_seen" not in result_source, "_handle_crawl_result 不应使用 self._global_seen"
    assert "hash_key in seen" in result_source, "_handle_crawl_result 应使用 seen 参数"


if __name__ == "__main__":
    test_crawler_has_no_instance_level_seen_set()
    print("[PASS] test_crawler_has_no_instance_level_seen_set")

    test_crawler_uses_arun_many_not_manual_worker_pool()
    print("[PASS] test_crawler_uses_arun_many_not_manual_worker_pool")

    test_seen_set_passed_as_parameter()
    print("[PASS] test_seen_set_passed_as_parameter")

    print("\n=== TDD Loop 3: Crawler session isolation tests passed! ===")
