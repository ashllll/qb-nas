"""装配契约：调用方引用的依赖方法必须在真实对象上存在。

背景：UserActionExecutor.ingest 调用 self._pipeline.ingest(...)，但真实
HarvestPipeline 没有该方法（只有测试里的假 pipeline 定义了它），因此该调用一旦
被接线就会 AttributeError。协议一致性检查发现不了它 —— ingest 不在任何 Protocol
里，而"满足协议"只保证协议声明的方法存在。

本测试直接对比源码中被引用的方法名与真实装配对象，覆盖这一类缺陷。
"""

from __future__ import annotations

import inspect
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester import assembly as asm
from magnet_harvester.services import clipboard_monitor as clipboard_module
from magnet_harvester.services import user_actions as user_actions_module

_KNOWN_MISSING = {
    # 已确认的历史遗留：该方法无任何生产调用方，属不可达代码，待决定删除或实现。
    # 记录为已知缺口而非直接跳过：一旦修复或删除，下面的缺口测试会失败提醒同步。
    ("UserActionExecutor", "ingest"),
}


def _referenced_pipeline_methods(cls) -> set[str]:
    return set(re.findall(r"self\._pipeline\.(\w+)", inspect.getsource(cls)))


@pytest.mark.parametrize(
    ("label", "cls"),
    [
        ("UserActionExecutor", user_actions_module.UserActionExecutor),
        ("ClipboardMonitor", clipboard_module.ClipboardMonitor),
    ],
)
def test_callers_only_reference_methods_present_on_real_pipeline(label, cls):
    """调用方引用的 pipeline 方法，必须真实存在于装配后的 pipeline 上。"""
    pipeline = asm.build_runtime().ctx.core.pipeline
    missing = {name for name in _referenced_pipeline_methods(cls) if not hasattr(pipeline, name)}

    unexpected = {(label, name) for name in missing if (label, name) not in _KNOWN_MISSING}
    assert not unexpected, f"{label} 引用了 pipeline 上不存在的方法: {sorted(unexpected)}"

    known_hits = sorted((label, name) for name in missing if (label, name) in _KNOWN_MISSING)
    if known_hits:
        pytest.xfail(f"已知未实现（无生产调用方）: {known_hits}")


def test_user_action_executor_ingest_is_the_documented_known_gap():
    """把已知缺口钉在具体位置，避免它悄悄扩散到其他调用点。"""
    source = inspect.getsource(user_actions_module.UserActionExecutor)
    assert "self._pipeline.ingest(" in source
    pipeline = asm.build_runtime().ctx.core.pipeline
    assert not hasattr(pipeline, "ingest"), (
        "HarvestPipeline 已有 ingest —— 请更新 _KNOWN_MISSING 并重新评估该缺口"
    )
