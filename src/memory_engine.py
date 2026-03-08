"""
Memory Engine
Handles all file I/O, tier management, scoring, archiving, and token estimation.

Memory File Structure
---------------------
memory/
  core.md      — IDENTITY + PROCEDURAL + SEMANTIC tiers (durable, rarely pruned)
  working.md   — EPISODIC + EPHEMERAL tiers (active session, subject to compaction)
  archive/
    YYYY-MM-DD_HHMMSS_<label>.md   — point-in-time snapshots
    index.json                     — searchable archive manifest
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────
# Tier definitions
# ─────────────────────────────────────────────

TIERS = {
    "IDENTITY":   {"file": "core",    "weight": 1.00, "description": "Who user/system is — never prune"},
    "PROCEDURAL": {"file": "core",    "weight": 0.95, "description": "How to do things — never prune"},
    "SEMANTIC":   {"file": "core",    "weight": 0.80, "description": "What is true — prune if contradicted"},
    "EPISODIC":   {"file": "working", "weight": 0.40, "description": "What happened — decays over time"},
    "EPHEMERAL":  {"file": "working", "weight": 0.10, "description": "Transient context — discard after session"},
}

CORE_TIERS    = [t for t, meta in TIERS.items() if meta["file"] == "core"]
WORKING_TIERS = [t for t, meta in TIERS.items() if meta["file"] == "working"]

CORE_TEMPLATE = """\
# Core Memory

## [IDENTITY]

## [PROCEDURAL]

## [SEMANTIC]
"""

WORKING_TEMPLATE = """\
# Working Memory

## [EPISODIC]

## [EPHEMERAL]
"""


# ─────────────────────────────────────────────
# MemoryEngine
# ─────────────────────────────────────────────

class MemoryEngine:
    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir   = Path(memory_dir)
        self.core_file    = self.memory_dir / "core.md"
        self.working_file = self.memory_dir / "working.md"
        self.archive_dir  = self.memory_dir / "archive"
        self.index_file   = self.archive_dir / "index.json"
        self._ensure_dirs()

    # ── Init ──────────────────────────────────

    def _ensure_dirs(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        if not self.core_file.exists():
            self.core_file.write_text(CORE_TEMPLATE)

        if not self.working_file.exists():
            self.working_file.write_text(WORKING_TEMPLATE)

        if not self.index_file.exists():
            self._write_index({"archives": [], "stats": {"total_compactions": 0}})

    # ── Read / Write ──────────────────────────

    def read_core(self) -> str:
        return self.core_file.read_text(encoding="utf-8")

    def read_working(self) -> str:
        return self.working_file.read_text(encoding="utf-8")

    def read_all(self) -> str:
        return f"{self.read_core()}\n\n---\n\n{self.read_working()}"

    def write_core(self, content: str):
        self.core_file.write_text(content.strip() + "\n", encoding="utf-8")

    def write_working(self, content: str):
        self.working_file.write_text(content.strip() + "\n", encoding="utf-8")

    # ── Deduplication ─────────────────────────

    def extract_tier_entries(self, text: str, tier: str) -> list[str]:
        """
        Extract entry content strings (without timestamps) from a tier section.
        Returns lowercased, stripped content for comparison.
        """
        header = f"## [{tier}]"
        if header not in text:
            return []
        start = text.index(header) + len(header)
        next_header = re.search(r"\n## \[", text[start:])
        end = start + next_header.start() if next_header else len(text)
        section = text[start:end]
        entries = []
        for line in section.splitlines():
            line = line.strip()
            if line.startswith("- "):
                match = re.match(r"^- \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*", line)
                if match:
                    entries.append(line[match.end():].strip().lower())
                else:
                    entries.append(line[2:].strip().lower())
        return entries

    def _is_duplicate(self, content: str, tier: str, text: str) -> bool:
        """
        Check if content is a duplicate of an existing entry in the tier.
        Uses exact match and substring containment for entries > 20 chars.
        """
        existing = self.extract_tier_entries(text, tier)
        normalised = content.strip().lower()
        if normalised in existing:
            return True
        if len(normalised) > 20:
            for e in existing:
                if normalised in e or e in normalised:
                    return True
        return False

    # ── Identity Initialisation ──────────────────────────────────────

    def is_initialized(self) -> bool:
        """
        Returns True if IDENTITY tier has been populated with at least one entry.
        Use this to detect first-run vs resuming a prior session.

        Example::

            if not engine.is_initialized():
                engine.init_identity(
                    identity="I am an assistant helping with X.",
                    procedural="I respond concisely. I cite sources.",
                )
        """
        try:
            sections = self.parse_sections(self.read_core())
            return bool(sections.get("IDENTITY", "").strip())
        except Exception:
            return False

    def init_identity(
        self,
        identity: str,
        procedural: str = "",
    ) -> bool:
        """
        Seed the IDENTITY (and optionally PROCEDURAL) tier on first use.
        No-op if IDENTITY is already populated — safe to call on every startup.

        Args:
            identity:   Who or what this agent/system is. Written to [IDENTITY].
            procedural: How this agent operates — rules, preferences, patterns.
                        Written to [PROCEDURAL]. Optional.

        Returns:
            True  if identity was written (first run).
            False if identity was already present (subsequent runs — no-op).

        Example::

            engine.init_identity(
                identity=(
                    "I am Aria, a customer support agent for Acme Corp. "
                    "I help users troubleshoot software issues."
                ),
                procedural=(
                    "I always ask clarifying questions before suggesting solutions. "
                    "I escalate to human support after 3 failed attempts."
                ),
            )
        """
        if self.is_initialized():
            return False

        self.append_memory(identity.strip(), tier="IDENTITY")
        if procedural.strip():
            self.append_memory(procedural.strip(), tier="PROCEDURAL")
        return True

    # ── Append ────────────────────────────────

    def append_memory(self, content: str, tier: str = "EPISODIC") -> bool:
        """
        Append a timestamped entry to the correct tier section.
        Deduplicates durable tiers (IDENTITY, PROCEDURAL, SEMANTIC) —
        skips if an identical or substring-matching entry already exists.
        Returns True if written, False if skipped as duplicate.
        """
        tier = tier.upper()
        if tier not in TIERS:
            raise ValueError(f"Unknown tier '{tier}'. Valid: {list(TIERS.keys())}")

        target = "core" if TIERS[tier]["file"] == "core" else "working"
        text   = self.read_core() if target == "core" else self.read_working()

        # Deduplicate durable tiers
        if tier in ("IDENTITY", "PROCEDURAL", "SEMANTIC"):
            if self._is_duplicate(content, tier, text):
                return False

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- [{timestamp}] {content.strip()}"

        updated = self._insert_into_section(text, tier, entry)

        if target == "core":
            self.write_core(updated)
        else:
            self.write_working(updated)
        return True

    def _insert_into_section(self, text: str, tier: str, entry: str) -> str:
        """Insert entry at the end of a tier section, before the next section."""
        header  = f"## [{tier}]"
        pattern = re.compile(
            rf"(## \[{re.escape(tier)}\])(.*?)(\n## \[|\Z)",
            re.DOTALL,
        )
        match = pattern.search(text)
        if not match:
            # Section missing — append it
            return text.rstrip() + f"\n\n{header}\n{entry}\n"

        existing_content = match.group(2)
        new_content      = existing_content.rstrip() + f"\n{entry}\n"
        return (
            text[: match.start(2)]
            + new_content
            + text[match.start(3):]
        )

    # ── Token / Size Estimation ───────────────

    def estimate_tokens(self, text: Optional[str] = None) -> int:
        """
        Estimate token count. Uses tiktoken if available, falls back to char/4.
        """
        if text is None:
            text = self.read_all()
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            return len(text) // 4

    def get_file_sizes(self) -> dict:
        return {
            "core":    self.core_file.stat().st_size if self.core_file.exists() else 0,
            "working": self.working_file.stat().st_size if self.working_file.exists() else 0,
        }

    def get_status(self) -> dict:
        sizes   = self.get_file_sizes()
        all_txt = self.read_all()
        return {
            "core_bytes":    sizes["core"],
            "working_bytes": sizes["working"],
            "total_bytes":   sizes["core"] + sizes["working"],
            "est_tokens":    self.estimate_tokens(all_txt),
            "archives":      len(self.list_archives()),
        }

    # ── Tier Parsing ──────────────────────────

    def parse_sections(self, text: str) -> dict[str, str]:
        """Parse a memory file into a dict of {TIER: content}."""
        sections = {}
        pattern  = re.compile(r"## \[([A-Z]+)\](.*?)(?=\n## \[|\Z)", re.DOTALL)
        for match in pattern.finditer(text):
            sections[match.group(1)] = match.group(2).strip()
        return sections

    def entry_count(self, tier: Optional[str] = None) -> dict:
        """Count bullet entries per tier."""
        counts = {}
        for t in TIERS:
            source = self.read_core() if TIERS[t]["file"] == "core" else self.read_working()
            counts[t] = len(self.extract_tier_entries(source, t))
        if tier:
            return {tier: counts.get(tier.upper(), 0)}
        return counts

    # ── Archive ───────────────────────────────

    def archive_snapshot(self, label: str = "snapshot") -> str:
        """
        Save current state to archive. Returns archive filename.
        Always call this before a destructive operation.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        # Sanitise label for filesystem
        safe_label = re.sub(r"[^a-zA-Z0-9_\-]", "_", label)[:32]
        filename   = f"{timestamp}_{safe_label}.md"
        path       = self.archive_dir / filename

        core    = self.read_core()
        working = self.read_working()

        path.write_text(
            f"# Memory Archive\n"
            f"Timestamp: {timestamp}\n"
            f"Label:     {label}\n\n"
            f"---\n\n"
            f"## CORE\n\n{core}\n\n"
            f"---\n\n"
            f"## WORKING\n\n{working}\n",
            encoding="utf-8",
        )

        # Update index
        index = self._read_index()
        index["archives"].append({
            "filename":    filename,
            "timestamp":   timestamp,
            "label":       label,
            "core_bytes":  len(core.encode()),
            "working_bytes": len(working.encode()),
        })
        self._write_index(index)

        return filename

    def list_archives(self) -> list:
        return self._read_index().get("archives", [])

    def restore_archive(self, filename: str):
        """
        Restore core and working memory from an archived snapshot.
        Automatically archives current state first as a safety measure.
        """
        path = self.archive_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Archive not found: {filename}")

        # Safety snapshot of current state
        self.archive_snapshot(label="pre_restore")

        content = path.read_text(encoding="utf-8")
        parts   = content.split("\n---\n")

        new_core    = None
        new_working = None

        for part in parts:
            if "## CORE" in part:
                new_core = re.sub(r"^.*?## CORE\s*\n", "", part, flags=re.DOTALL).strip()
            elif "## WORKING" in part:
                new_working = re.sub(r"^.*?## WORKING\s*\n", "", part, flags=re.DOTALL).strip()

        if new_core:
            self.write_core(new_core)
        if new_working:
            self.write_working(new_working)

    def increment_compaction_count(self):
        index = self._read_index()
        index.setdefault("stats", {})
        index["stats"]["total_compactions"] = index["stats"].get("total_compactions", 0) + 1
        index["stats"]["last_compaction"] = datetime.now().isoformat()
        self._write_index(index)

    # ── Index helpers ─────────────────────────

    def _read_index(self) -> dict:
        return json.loads(self.index_file.read_text(encoding="utf-8"))

    def _write_index(self, data: dict):
        self.index_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
