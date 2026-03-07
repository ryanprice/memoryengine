"""
Compactor
---------
Implements the two-prompt compaction pipeline:
  1. EXTRACT   — pull net-new durable memories from a session context
  2. COMPACT   — compress working memory, promote durable facts to core

Compaction triggers (checked in order):
  - Token threshold exceeded
  - File size threshold exceeded
  - Manual (--force)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory_engine import MemoryEngine
    from .providers import LLMProvider


# ─────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────

EXTRACTION_SYSTEM = """\
You are a memory distillation engine for an AI agent.

Your job: read a session context and extract ONLY net-new durable knowledge \
not already captured in existing memory.

Memory tiers (from most to least durable):
  [IDENTITY]   — Who the user/system is. Name, role, persistent goals. NEVER discard.
  [PROCEDURAL] — How to do things. Explicit rules, preferences, workflows. NEVER discard.
  [SEMANTIC]   — What is true. Decisions, facts, architectural choices. Discard only if contradicted.
  [EPISODIC]   — What happened. Event summaries, session outcomes. Decays over time.
  [EPHEMERAL]  — Transient context. Raw outputs, one-time lookups. Discard after session.

Rules:
- Do NOT repeat anything already in existing memory.
- Do NOT invent facts not present in the session context.
- If a fact contradicts existing memory, note it with prefix: [SUPERSEDES: <old fact summary>]
- Keep entries concise — one line per fact.
- If a tier has nothing new, leave it empty but keep the header.

Output ONLY this markdown block. No preamble, no explanation, no reasoning:

## [IDENTITY]
<new entries or empty>

## [PROCEDURAL]
<new entries or empty>

## [SEMANTIC]
<new entries or empty>

## [EPISODIC]
<new entries or empty>

## [EPHEMERAL]
<new entries or empty>
"""

COMPACTION_SYSTEM = """\
You are a memory compaction engine for an AI agent.

Your job: compress a working memory file without losing durable knowledge.

Compaction rules — apply in order:
1. [EPHEMERAL] — DELETE all entries. No exceptions.
2. [EPISODIC]  — Cluster related events and summarise into a single higher-level insight. \
Convert recurring patterns into a [SEMANTIC] fact instead.
3. [SEMANTIC]  — Merge near-duplicate entries. Remove entries explicitly contradicted by \
a newer [SUPERSEDES] annotation. If you are uncertain, KEEP the entry.
4. [PROCEDURAL] / [IDENTITY] — NEVER remove unless exact duplicate text.

Before removing any entry, ask: "Could this be recovered from a tool call or document?" \
If NO → keep it. If YES → safe to discard.

Output ONLY the compacted working memory markdown. \
Maintain tier headers: ## [EPISODIC] and ## [EPHEMERAL]. \
No preamble, no explanation.
"""

MERGE_SYSTEM = """\
You are a memory merge engine. You receive two memory files — core and compacted working — \
and must:

1. Move any [SEMANTIC] or [PROCEDURAL] entries from working into the correct section of core.
2. Remove moved entries from working.
3. Deduplicate: if the same fact exists in both, keep the more detailed version in core only.
4. Resolve contradictions: if working has a [SUPERSEDES] note, update core accordingly \
and remove the superseded entry from core.

Output EXACTLY two sections separated by these delimiters (include delimiters in output):

<<<CORE>>>
<full updated core memory markdown>

<<<WORKING>>>
<full updated working memory markdown>
"""


# ─────────────────────────────────────────────
# Compactor
# ─────────────────────────────────────────────

class Compactor:
    def __init__(self, engine: "MemoryEngine", provider: "LLMProvider"):
        self.engine   = engine
        self.provider = provider

    # ── Trigger Detection ─────────────────────

    def should_compact(self, config: dict) -> tuple[bool, str]:
        """
        Returns (should_compact: bool, reason: str).
        Checks token threshold, then file size threshold.
        """
        sizes      = self.engine.get_file_sizes()
        all_text   = self.engine.read_all()
        token_est  = self.engine.estimate_tokens(all_text)

        token_threshold = config.get("token_threshold", 6000)
        size_threshold  = config.get("size_threshold_bytes", 40_000)

        if token_est >= token_threshold:
            return True, f"estimated tokens ({token_est:,}) ≥ threshold ({token_threshold:,})"

        if sizes["working"] >= size_threshold:
            return True, (
                f"working file size ({sizes['working']:,} bytes) "
                f"≥ threshold ({size_threshold:,})"
            )

        return False, (
            f"within limits — ~{token_est:,} tokens, "
            f"{sizes['working']:,} bytes working"
        )

    # ── Extract ───────────────────────────────

    def extract(self, session_context: str, auto_apply: bool = False) -> str:
        """
        Extract net-new durable memories from raw session context.
        Returns the extracted markdown block.
        If auto_apply=True, appends extracted content to working memory.
        """
        existing = self.engine.read_all()
        user_prompt = (
            f"EXISTING MEMORY:\n{existing}\n\n"
            f"SESSION CONTEXT TO PROCESS:\n{session_context}\n\n"
            "Extract ONLY net-new knowledge not already captured in existing memory."
        )
        extracted = self.provider.complete(EXTRACTION_SYSTEM, user_prompt, max_tokens=2000)

        if auto_apply:
            self._apply_extracted(extracted)

        return extracted

    def _apply_extracted(self, extracted: str):
        """Merge extracted tiers into the correct memory files."""
        sections = self._parse_tier_sections(extracted)

        core_tiers    = ["IDENTITY", "PROCEDURAL", "SEMANTIC"]
        working_tiers = ["EPISODIC", "EPHEMERAL"]

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for tier in core_tiers:
            content = sections.get(tier, "").strip()
            if content:
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # Already has a bullet? respect it; otherwise add one
                        entry = line if line.startswith("-") else f"- [{timestamp}] {line}"
                        self.engine.append_memory(
                            entry.lstrip("- ").lstrip(), tier=tier
                        )

        for tier in working_tiers:
            content = sections.get(tier, "").strip()
            if content:
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        entry = line if line.startswith("-") else f"- [{timestamp}] {line}"
                        self.engine.append_memory(
                            entry.lstrip("- ").lstrip(), tier=tier
                        )

    # ── Compact ───────────────────────────────

    def compact(self) -> dict:
        """
        Full compaction cycle:
          1. Archive pre-compaction state
          2. Compact working memory
          3. Merge promoted entries into core
          4. Archive post-compaction state
          5. Return stats
        """
        before_sizes   = self.engine.get_file_sizes()
        before_tokens  = self.engine.estimate_tokens()
        archive_before = self.engine.archive_snapshot("pre_compact")

        # ── Step 1: Compact working memory ──
        working           = self.engine.read_working()
        compacted_working = self.provider.complete(
            COMPACTION_SYSTEM,
            f"Compact this working memory:\n\n{working}",
            max_tokens=3000,
        )

        # ── Step 2: Merge into core ──
        core         = self.engine.read_core()
        merge_prompt = (
            f"CURRENT CORE MEMORY:\n{core}\n\n"
            f"COMPACTED WORKING MEMORY:\n{compacted_working}\n\n"
            "Merge promoted entries from working into core. "
            "Resolve contradictions. Output <<<CORE>>> and <<<WORKING>>> blocks."
        )
        merged = self.provider.complete(MERGE_SYSTEM, merge_prompt, max_tokens=5000)

        # ── Step 3: Parse and write ──
        new_core, new_working = self._parse_merge_output(merged, core, compacted_working)
        self.engine.write_core(new_core)
        self.engine.write_working(new_working)

        after_sizes  = self.engine.get_file_sizes()
        after_tokens = self.engine.estimate_tokens()
        archive_after = self.engine.archive_snapshot("post_compact")
        self.engine.increment_compaction_count()

        return {
            "archive_before":  archive_before,
            "archive_after":   archive_after,
            "provider":        self.provider.name,
            "before": {
                "core_bytes":    before_sizes["core"],
                "working_bytes": before_sizes["working"],
                "est_tokens":    before_tokens,
            },
            "after": {
                "core_bytes":    after_sizes["core"],
                "working_bytes": after_sizes["working"],
                "est_tokens":    after_tokens,
            },
            "reduction": {
                "bytes":  (before_sizes["core"] + before_sizes["working"])
                         - (after_sizes["core"] + after_sizes["working"]),
                "tokens": before_tokens - after_tokens,
            },
        }

    # ── Helpers ───────────────────────────────

    def _parse_merge_output(
        self,
        merged: str,
        fallback_core: str,
        fallback_working: str,
    ) -> tuple[str, str]:
        """Parse <<<CORE>>> / <<<WORKING>>> delimiters from merge output."""
        if "<<<CORE>>>" in merged and "<<<WORKING>>>" in merged:
            try:
                core_part    = merged.split("<<<CORE>>>")[1].split("<<<WORKING>>>")[0].strip()
                working_part = merged.split("<<<WORKING>>>")[1].strip()
                if core_part and working_part:
                    return core_part, working_part
            except (IndexError, ValueError):
                pass
        # Fallback: LLM didn't respect delimiters — keep compacted working, original core
        return fallback_core, fallback_working

    def _parse_tier_sections(self, text: str) -> dict[str, str]:
        """Parse tier headers from LLM output into {TIER: content} dict."""
        sections = {}
        pattern  = re.compile(r"## \[([A-Z]+)\](.*?)(?=\n## \[|\Z)", re.DOTALL)
        for match in pattern.finditer(text):
            sections[match.group(1)] = match.group(2).strip()
        return sections
