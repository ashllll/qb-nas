"""Runtime dependency manifest consistency tests."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _dependency_name(spec: str) -> str:
    return re.split(r"[<>=!~;\[]", spec, maxsplit=1)[0].strip().lower()


def test_startup_entrypoint_reports_real_bind_address():
    """run.py 必须把真实绑定地址告知应用（MH_BOUND_HOST）。

    启动期鉴权强制依据真实绑定地址：非 loopback 且无 API_KEY 时必须拒绝启动。
    若启动入口不再传递该地址，校验会退回按 SERVICE_HOST 判断，从而可被
    `uvicorn --host 0.0.0.0` 之类的绑定方式绕过，把无鉴权写接口暴露到 LAN。
    """
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    # 必须断言"确实写入环境变量"，而非仅出现该标识符：
    # 若只查子串，改名成 MH_BOUND_HOST_TEMP 之类仍会通过（空检查）。
    assert re.search(r"""os\.environ\[["']MH_BOUND_HOST["']\]""", source), (
        "run.py 未把真实绑定地址写入 MH_BOUND_HOST，启动期鉴权强制会失效"
    )


def test_docs_do_not_recommend_direct_uvicorn_host_override():
    """文档不得把 `uvicorn ... --host` 列为可复制执行的启动方式。

    该路径下应用拿不到真实绑定地址，启动期鉴权强制失效（见 docs/verification.md）。

    断言的是"命令行"而不是某句提示语：在 AGENTS.md / CLAUDE.md / README.md /
    docs/verification.md 中，任何含字面 `uvicorn magnet_harvester.main:app` 的行都
    必须在本行或紧邻上一行标注为非受支持/禁止用法。只做整句子串检查会被提示语自身
    满足（空检查），因此强制"命令与标注相邻出现"。
    """
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "python run.py" in agents
    assert "不要用 `uvicorn" in agents, "AGENTS.md 应明确标注不要用 uvicorn --host 直接启动"

    command = re.compile(r"(?:python -m )?uvicorn\s+magnet_harvester\.main:app")
    markers = ("不要用", "禁止", "非受支持")
    docs = (
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "README.md",
        ROOT / "docs" / "verification.md",
    )
    for doc in docs:
        lines = doc.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not command.search(stripped):
                continue
            context = stripped + (lines[index - 1] if index else "")
            assert any(word in context for word in markers), (
                f"{doc.name}:{index + 1} 出现未标注为非受支持用法的 uvicorn 启动命令: {stripped!r}"
            )


def test_requirements_and_pyproject_runtime_dependencies_are_in_sync():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_deps = {_dependency_name(spec) for spec in pyproject["project"]["dependencies"]}
    requirements = {
        _dependency_name(line)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert requirements == pyproject_deps


def test_npm_quality_gate_uses_cross_platform_python_launcher():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["lint"].startswith("node scripts/run-python.cjs ")
    assert package["scripts"]["test"].startswith("node scripts/run-python.cjs ")
    assert package["scripts"]["coverage"].startswith("node scripts/run-python.cjs ")
    assert "--cov" in package["scripts"]["coverage"]
    assert package["scripts"]["lock:check"] == "uv lock --check"
    assert package["scripts"]["check"] == ("npm run lock:check && npm run lint && npm run coverage")
    assert ".venv/bin" not in str(package["scripts"])


def test_coverage_gate_is_declared_in_dev_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = {
        _dependency_name(spec) for spec in pyproject["project"]["optional-dependencies"]["dev"]
    }

    assert "pytest-cov" in dev_dependencies
    assert pyproject["tool"]["coverage"]["run"]["source"] == ["magnet_harvester"]
    assert pyproject["tool"]["coverage"]["report"]["fail_under"] >= 79


def test_uv_lock_tracks_all_declared_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    project = next(package for package in lock["package"] if package["name"] == "magnet-harvester")
    locked_names = {dependency["name"] for dependency in project["metadata"]["requires-dist"]}
    declared = {
        _dependency_name(spec)
        for spec in (
            pyproject["project"]["dependencies"]
            + pyproject["project"]["optional-dependencies"]["dev"]
        )
    }

    assert project["source"] == {"editable": "."}
    assert locked_names == declared


def test_python_launcher_forwards_child_exit_code():
    result = subprocess.run(
        ["node", "scripts/run-python.cjs", "-c", "import sys; sys.exit(7)"],
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 7


def test_python_launcher_fails_clearly_without_local_virtualenv(tmp_path):
    result = subprocess.run(
        ["node", str(ROOT / "scripts" / "run-python.cjs"), "--version"],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
    )

    expected = ".venv\\Scripts\\python.exe" if sys.platform == "win32" else ".venv/bin/python"
    assert result.returncode != 0
    assert f"Python virtual environment not found: {expected}" in result.stderr
