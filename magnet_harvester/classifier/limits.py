"""分类器输入上限。

发布名来自不可控的外部（网页标题、磁力的 ``dn``），长度没有上限。分类规则里
有惰性嵌套量词（工作室名 + 日期），在没有日期的长串上会退化成平方级：
实测 8KB ≈ 1.1 秒、64KB ≈ 71 秒，而分类是同步跑在事件循环里的——一次就把
WebSocket、API、qB 同步全部堵死。

分类只关心发布名开头的那点信息（片名/剧集/格式/厂牌），截到 512 字符既不改
判类结果，也让规则耗时与长度线性。
"""

from __future__ import annotations

MAX_CLASSIFY_CHARS = 512


def clip_name(name: str) -> str:
    """把分类输入截到上限；None/非字符串按空串处理。"""
    if not name:
        return ""
    return str(name)[:MAX_CLASSIFY_CHARS]
