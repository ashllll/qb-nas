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
    assert ".venv/bin" not in str(package["scripts"])


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
