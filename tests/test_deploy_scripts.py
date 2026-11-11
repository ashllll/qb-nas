"""deploy/uninstall.sh 的根目录校验：--prefix 不安全时必须在任何 rm 之前退出。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "uninstall.sh"


def _run(prefix: str, *args: str) -> subprocess.CompletedProcess[str]:
    stub = Path(os.environ.get("PYTEST_TMP", "/tmp")) / "deploy-stub"
    # 一个必然失败的 docker：脚本会走「跳过 docker 操作」分支，删除逻辑仍会执行
    stub.mkdir(parents=True, exist_ok=True)
    docker = stub / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n")
    docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{stub}:/usr/bin:/bin:/usr/sbin:/sbin")
    return subprocess.run(
        ["bash", str(SCRIPT), "--prefix", prefix, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")
def test_refuses_root_prefix(tmp_path: Path) -> None:
    r = _run("/", "--purge", "--yes")
    assert r.returncode != 0
    assert "至少两级" in r.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")
def test_refuses_single_level_and_dotdot(tmp_path: Path) -> None:
    for prefix in ("/vol3", "/vol3/1000/../../.."):
        r = _run(prefix, "--purge", "--yes")
        assert r.returncode != 0, prefix
        assert "✗" in r.stderr and prefix.rstrip("/") in r.stderr, prefix


@pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")
def test_keeps_data_and_removes_program(tmp_path: Path) -> None:
    prefix = tmp_path / "qb-nas"
    (prefix / "app").mkdir(parents=True)
    (prefix / "app" / "main.py").write_text("x")
    (prefix / "run").mkdir()
    (prefix / "compose.yaml").write_text("services: {}\n")
    (prefix / "data").mkdir()
    (prefix / "data" / "magnet_items.db").write_text("db")
    (prefix / "api-key.txt").write_text("key")

    r = _run(str(prefix))
    assert r.returncode == 0, r.stderr
    assert not (prefix / "app").exists()
    assert not (prefix / "run").exists()
    assert not (prefix / "compose.yaml").exists()
    assert (prefix / "data" / "magnet_items.db").read_text() == "db"
    assert (prefix / "api-key.txt").read_text() == "key"
