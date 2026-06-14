"""
Test error_handler wiring through AppContext — verifies no module-level singleton.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.errors import ErrorHandler, ErrorCategory, ErrorSeverity
from magnet_harvester.store import FakeStore
from magnet_harvester.bus import NullBus

# ── 1. ErrorHandler works standalone (no module-level global required) ──

def test_error_handler_standalone_record():
    """ErrorHandler instances are independently usable — no singleton needed."""
    eh = ErrorHandler()
    eid = eh.record(
        ErrorCategory.QBIT,
        ErrorSeverity.ERROR,
        "qBittorrent connection refused",
    )
    assert eid
    stats = eh.get_error_stats()
    assert stats["unique_errors"] == 1
    assert stats["total_errors"] == 1


def test_error_handler_standalone_clear():
    """clear_resolved works on independent instances."""
    eh = ErrorHandler()
    eh.record(ErrorCategory.CRAWLER, ErrorSeverity.WARNING, "test")
    recent = eh.get_recent_errors(limit=10)
    assert len(recent) == 1
    eh.clear_resolved()
    # clear_resolved only drops items where resolved=True — noop for default
    recent2 = eh.get_recent_errors(limit=10)
    assert len(recent2) == 1  # still there (resolved=False by default)


def test_error_handler_instances_are_independent():
    """Two ErrorHandler instances don't share state."""
    eh1 = ErrorHandler()
    eh2 = ErrorHandler()
    eh1.record(ErrorCategory.QBIT, ErrorSeverity.ERROR, "only in eh1")
    assert eh1.get_error_stats()["unique_errors"] == 1
    assert eh2.get_error_stats()["unique_errors"] == 0


# ── 2. AppContext can carry error_handler ──

def test_appcontext_accepts_error_handler():
    """AppContext.error_handler field works — no import of module-level singleton."""
    from magnet_harvester.context.app_context import AppContext

    eh = ErrorHandler()
    ctx = AppContext(
        store=FakeStore(),
        bus=NullBus(),
        pipeline=None,
        crawler=None,
        classifier=None,
        qbit=None,
        error_handler=eh,
    )
    assert ctx.error_handler is eh
    # Verify the field is usable through context
    stats = ctx.error_handler.get_error_stats()
    assert isinstance(stats, dict)


# ── 3. No module-level error_handler global ──

def test_no_module_level_error_handler_global():
    """errors.py should NOT export a module-level error_handler singleton."""
    import magnet_harvester.errors as err_mod
    # After the refactor, error_handler should NOT exist at module level
    assert not hasattr(err_mod, "error_handler"), (
        "Module-level error_handler singleton detected — it should be wired through AppContext instead"
    )


if __name__ == "__main__":
    test_error_handler_standalone_record()
    test_error_handler_standalone_clear()
    test_error_handler_instances_are_independent()
    test_appcontext_accepts_error_handler()
    test_no_module_level_error_handler_global()
    print("=== error_handler context tests passed! ===")
