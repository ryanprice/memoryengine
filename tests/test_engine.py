"""
Tests for Memory Engine
Run: python -m pytest tests/ -v
"""

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory_engine import MemoryEngine, CORE_TEMPLATE, WORKING_TEMPLATE


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

def make_engine():
    """Create an engine in a temporary directory."""
    tmpdir = tempfile.mkdtemp()
    return MemoryEngine(memory_dir=tmpdir + "/memory"), tmpdir


# ─────────────────────────────────────────────
# MemoryEngine tests
# ─────────────────────────────────────────────

class TestMemoryEngine:

    def test_init_creates_files(self):
        engine, _ = make_engine()
        assert engine.core_file.exists()
        assert engine.working_file.exists()
        assert engine.archive_dir.exists()
        assert engine.index_file.exists()

    def test_init_creates_correct_structure(self):
        engine, _ = make_engine()
        core    = engine.read_core()
        working = engine.read_working()
        assert "## [IDENTITY]" in core
        assert "## [PROCEDURAL]" in core
        assert "## [SEMANTIC]" in core
        assert "## [EPISODIC]" in working
        assert "## [EPHEMERAL]" in working

    def test_append_to_core_tiers(self):
        engine, _ = make_engine()
        engine.append_memory("User is Ryan", tier="IDENTITY")
        engine.append_memory("Always use async patterns", tier="PROCEDURAL")
        engine.append_memory("Project uses JWT auth", tier="SEMANTIC")
        core = engine.read_core()
        assert "User is Ryan" in core
        assert "Always use async patterns" in core
        assert "Project uses JWT auth" in core

    def test_append_to_working_tiers(self):
        engine, _ = make_engine()
        engine.append_memory("Debugged auth issue today", tier="EPISODIC")
        engine.append_memory("Temp search result", tier="EPHEMERAL")
        working = engine.read_working()
        assert "Debugged auth issue today" in working
        assert "Temp search result" in working

    def test_append_invalid_tier_raises(self):
        engine, _ = make_engine()
        try:
            engine.append_memory("test", tier="INVALID")
            assert False, "Should have raised"
        except ValueError:
            pass

    def test_multiple_entries_per_tier(self):
        engine, _ = make_engine()
        engine.append_memory("Fact A", tier="SEMANTIC")
        engine.append_memory("Fact B", tier="SEMANTIC")
        engine.append_memory("Fact C", tier="SEMANTIC")
        core = engine.read_core()
        assert core.count("Fact A") == 1
        assert core.count("Fact B") == 1
        assert core.count("Fact C") == 1

    def test_entry_count(self):
        engine, _ = make_engine()
        engine.append_memory("Entry 1", tier="SEMANTIC")
        engine.append_memory("Entry 2", tier="SEMANTIC")
        engine.append_memory("Entry 3", tier="EPISODIC")
        counts = engine.entry_count()
        assert counts["SEMANTIC"] == 2
        assert counts["EPISODIC"] == 1
        assert counts["IDENTITY"] == 0

    def test_estimate_tokens(self):
        engine, _ = make_engine()
        tokens = engine.estimate_tokens("Hello world")
        assert tokens > 0

    def test_get_file_sizes(self):
        engine, _ = make_engine()
        sizes = engine.get_file_sizes()
        assert sizes["core"] > 0
        assert sizes["working"] > 0

    def test_archive_snapshot(self):
        engine, _ = make_engine()
        engine.append_memory("Test entry", tier="SEMANTIC")
        filename = engine.archive_snapshot("test_label")
        assert (engine.archive_dir / filename).exists()
        archives = engine.list_archives()
        assert len(archives) == 1
        assert archives[0]["label"] == "test_label"

    def test_archive_contains_content(self):
        engine, _ = make_engine()
        engine.append_memory("Memorable fact", tier="SEMANTIC")
        filename = engine.archive_snapshot("check_content")
        content = (engine.archive_dir / filename).read_text()
        assert "Memorable fact" in content

    def test_restore_archive(self):
        engine, _ = make_engine()
        engine.append_memory("Original fact", tier="SEMANTIC")
        filename = engine.archive_snapshot("before_change")

        # Overwrite
        engine.write_core(CORE_TEMPLATE)
        assert "Original fact" not in engine.read_core()

        # Restore
        engine.restore_archive(filename)
        assert "Original fact" in engine.read_core()

    def test_restore_creates_safety_archive(self):
        engine, _ = make_engine()
        filename = engine.archive_snapshot("original")
        engine.restore_archive(filename)
        archives = engine.list_archives()
        labels = [a["label"] for a in archives]
        assert "pre_restore" in labels

    def test_parse_sections(self):
        engine, _ = make_engine()
        engine.append_memory("Identity fact", tier="IDENTITY")
        engine.append_memory("Semantic fact", tier="SEMANTIC")
        core     = engine.read_core()
        sections = engine.parse_sections(core)
        assert "IDENTITY" in sections
        assert "Identity fact" in sections["IDENTITY"]

    def test_read_all_combines_files(self):
        engine, _ = make_engine()
        engine.append_memory("Core fact", tier="SEMANTIC")
        engine.append_memory("Working fact", tier="EPISODIC")
        combined = engine.read_all()
        assert "Core fact" in combined
        assert "Working fact" in combined


# ─────────────────────────────────────────────
# Compactor tests (no LLM — mock provider)
# ─────────────────────────────────────────────

class MockProvider:
    """Provider that returns predictable outputs for testing."""

    @property
    def name(self):
        return "mock/test"

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        if "<<<CORE>>>" in system or "merge" in system.lower():
            return (
                "<<<CORE>>>\n# Core Memory\n\n## [IDENTITY]\n\n## [PROCEDURAL]\n\n## [SEMANTIC]\n\n"
                "<<<WORKING>>>\n# Working Memory\n\n## [EPISODIC]\n\n## [EPHEMERAL]\n"
            )
        return (
            "## [IDENTITY]\n\n"
            "## [PROCEDURAL]\n\n"
            "## [SEMANTIC]\n- Extracted semantic fact\n\n"
            "## [EPISODIC]\n- Extracted episodic entry\n\n"
            "## [EPHEMERAL]\n"
        )


class TestCompactor:

    def test_should_compact_token_threshold(self):
        from src.compactor import Compactor
        engine, _ = make_engine()
        compactor = Compactor(engine, MockProvider())

        # Force large working file
        large_text = "- [2024-01-01] " + ("word " * 500) + "\n"
        engine.write_working("# Working Memory\n\n## [EPISODIC]\n" + large_text * 10 + "\n## [EPHEMERAL]\n")

        should, reason = compactor.should_compact({"token_threshold": 100})
        assert should is True
        assert "token" in reason.lower()

    def test_should_not_compact_when_small(self):
        from src.compactor import Compactor
        engine, _ = make_engine()
        compactor = Compactor(engine, MockProvider())
        should, reason = compactor.should_compact({
            "token_threshold": 100_000,
            "size_threshold_bytes": 10_000_000,
        })
        assert should is False

    def test_extract_returns_string(self):
        from src.compactor import Compactor
        engine, _ = make_engine()
        compactor = Compactor(engine, MockProvider())
        result = compactor.extract("User said they like Python over TypeScript.")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compact_creates_archives(self):
        from src.compactor import Compactor
        engine, _ = make_engine()
        compactor = Compactor(engine, MockProvider())
        stats = compactor.compact()
        assert "archive_before" in stats
        assert "archive_after" in stats
        archives = engine.list_archives()
        assert len(archives) >= 2

    def test_compact_returns_stats(self):
        from src.compactor import Compactor
        engine, _ = make_engine()
        compactor = Compactor(engine, MockProvider())
        stats = compactor.compact()
        assert "before" in stats
        assert "after" in stats
        assert "reduction" in stats
        assert "est_tokens" in stats["before"]


if __name__ == "__main__":
    import unittest

    # Run without pytest
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestMemoryEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestCompactor))
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
