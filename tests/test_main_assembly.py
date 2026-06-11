"""Main app should delegate runtime wiring to a dedicated assembly helper."""
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent


def test_main_uses_dedicated_application_assembly_helper():
    main_source = (REPO_ROOT / "magnet_harvester/main.py").read_text(encoding="utf-8")

    assert "magnet_harvester.assembly" in main_source
    assert "build_runtime" in main_source or "assemble_runtime" in main_source


def test_assembly_module_exists():
    assembly_file = REPO_ROOT / "magnet_harvester/assembly.py"
    assert assembly_file.exists()
