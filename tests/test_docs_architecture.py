"""Documentation should reflect the centralized lifespan architecture."""

from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent


def test_agents_md_does_not_point_route_work_to_main_py():
    content = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Implement handler in `main.py`" not in content
    assert "Always use `_bg(coro, name)` helper in `main.py`" not in content


def test_readme_mentions_current_runtime_module_split():
    content = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "api/" in content
    assert "services/" in content
    assert "context/" in content
    assert "utils/" in content


def test_context_and_adr_reference_current_runtime_terms():
    context = (REPO_ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    adr = (REPO_ROOT / "docs/adr/ADR-0001-centralized-assembly-in-lifespan.md").read_text(
        encoding="utf-8"
    )

    assert "WSBroadcaster" in context
    assert "QBitSyncLoop" in context
    assert "UserActionExecutor" in context
    assert "BGTaskManager" in context
    assert "lifespan()" in adr
    assert "sole assembler" in adr
