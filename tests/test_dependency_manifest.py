"""Runtime dependency manifest consistency tests."""

from __future__ import annotations

import re
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
