"""Docker 镜像必须带分类关键词配置（#53）。

容器里 KEYWORD_FILE 指向 /app/config/category_keywords.json，这个文件不在 pip 包里，
只能由 Dockerfile 显式 COPY；.dockerignore 一旦把它排掉，容器内 KeywordRule 静默失效。
"""

from pathlib import Path

from magnet_harvester.classifier.keyword_recognizer import KeywordCategoryRecognizer

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_copies_keyword_config():
    dockerfile = (ROOT / "deploy/docker/Dockerfile").read_text(encoding="utf-8")
    assert "COPY --chown=1000:1001 config ./config" in dockerfile


def test_dockerignore_only_passes_keyword_config_from_config_dir():
    lines = [
        line.strip()
        for line in (ROOT / "deploy/docker/dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # 目录级排除（config/）无法再用 ! 放行里面的单个文件，所以必须按文件排除
    assert "config/" not in lines
    assert "config/*" in lines
    assert "!config/category_keywords.json" in lines


def test_repo_keyword_config_classifies_known_seed():
    recognizer = KeywordCategoryRecognizer()
    assert recognizer.recognize("Ubuntu.24.04.Desktop.iso") == {
        "category": "软件",
        "save_path": "软件",
        "keyword": "ubuntu",
    }
