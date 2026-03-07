#!/usr/bin/env python3
"""
Memory Engine CLI
-----------------
Usage examples:

  # Write a memory directly
  python session.py write "User prefers async/await over callbacks" --tier PROCEDURAL

  # Extract memories from a session log (file or stdin)
  python session.py extract session_log.txt
  cat session.txt | python session.py extract -

  # Run compaction (auto-checks thresholds unless --force)
  python session.py compact
  python session.py compact --force

  # Check current state
  python session.py status

  # Read memory files
  python session.py read
  python session.py read --file core
  python session.py read --file working

  # Archive management
  python session.py list-archives
  python session.py restore 2024-03-07_120000_pre_compact.md

  # Wipe working memory (keeps core)
  python session.py clear-working
"""

import argparse
import sys
import os
from pathlib import Path

# Allow running from repo root without installing
sys.path.insert(0, str(Path(__file__).parent))


def load_config(config_path: str = "config.yaml") -> dict:
    try:
        import yaml
        if Path(config_path).exists():
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
    except ImportError:
        print("⚠ PyYAML not installed — using default config. Run: pip install pyyaml")
    return {}


def get_engine_and_compactor(config: dict):
    from src.memory_engine import MemoryEngine
    from src.providers import get_provider
    from src.compactor import Compactor

    memory_dir = config.get("memory_dir", "memory")
    engine     = MemoryEngine(memory_dir=memory_dir)
    provider   = get_provider(config)
    compactor  = Compactor(engine, provider)
    return engine, compactor


def cmd_write(args, config):
    from src.memory_engine import MemoryEngine
    from src.providers import get_provider
    from src.compactor import Compactor

    memory_dir = config.get("memory_dir", "memory")
    engine     = MemoryEngine(memory_dir=memory_dir)
    engine.append_memory(args.content, tier=args.tier)
    print(f"✓ Written to [{args.tier}]")

    # Auto-check thresholds after write
    provider  = get_provider(config)
    compactor = Compactor(engine, provider)
    should, reason = compactor.should_compact(config)
    if should:
        print(f"⚠  Compaction recommended: {reason}")
        print("   Run: python session.py compact")


def cmd_extract(args, config):
    engine, compactor = get_engine_and_compactor(config)

    if args.source == "-":
        context = sys.stdin.read()
    else:
        path = Path(args.source)
        if not path.exists():
            print(f"✗ File not found: {args.source}")
            sys.exit(1)
        context = path.read_text(encoding="utf-8")

    if not context.strip():
        print("✗ Empty context — nothing to extract.")
        sys.exit(1)

    print(f"Extracting memories from context ({len(context):,} chars)...")
    extracted = compactor.extract(context, auto_apply=False)

    print("\n─── EXTRACTED MEMORIES ──────────────────────────────────")
    print(extracted)
    print("─────────────────────────────────────────────────────────\n")

    if args.yes:
        compactor._apply_extracted(extracted)
        print("✓ Memories applied automatically (--yes flag)")
    else:
        answer = input("Apply these memories? [y/N]: ").strip().lower()
        if answer == "y":
            compactor._apply_extracted(extracted)
            print("✓ Memories applied")
        else:
            print("Discarded.")

    # Auto-check after applying
    should, reason = compactor.should_compact(config)
    if should:
        print(f"⚠  Compaction recommended: {reason}")
        print("   Run: python session.py compact")


def cmd_compact(args, config):
    engine, compactor = get_engine_and_compactor(config)

    if not args.force:
        should, reason = compactor.should_compact(config)
        if not should:
            print(f"Compaction not needed: {reason}")
            if args.yes:
                pass
            else:
                answer = input("Compact anyway? [y/N]: ").strip().lower()
                if answer != "y":
                    print("Cancelled.")
                    return

    print(f"Running compaction (provider: {compactor.provider.name})...")
    stats = compactor.compact()

    print("✓ Compaction complete\n")
    print(f"  Provider:          {stats['provider']}")
    print(f"  Archive (before):  {stats['archive_before']}")
    print(f"  Archive (after):   {stats['archive_after']}")
    print(f"  Token reduction:   {stats['before']['est_tokens']:,} → {stats['after']['est_tokens']:,} "
          f"(−{stats['reduction']['tokens']:,})")
    print(f"  Byte reduction:    {stats['before']['core_bytes'] + stats['before']['working_bytes']:,} → "
          f"{stats['after']['core_bytes'] + stats['after']['working_bytes']:,} "
          f"(−{stats['reduction']['bytes']:,})")


def cmd_status(args, config):
    from src.memory_engine import MemoryEngine
    from src.providers import get_provider
    from src.compactor import Compactor

    memory_dir = config.get("memory_dir", "memory")
    engine     = MemoryEngine(memory_dir=memory_dir)
    status     = engine.get_status()
    counts     = engine.entry_count()

    token_threshold = config.get("token_threshold", 6000)
    size_threshold  = config.get("size_threshold_bytes", 40_000)

    provider  = get_provider(config)
    compactor = Compactor(engine, provider)
    should, reason = compactor.should_compact(config)

    print("═══ Memory Engine Status ══════════════════════════")
    print(f"  Provider:        {provider.name}")
    print(f"  Memory dir:      {memory_dir}/")
    print(f"  Core memory:     {status['core_bytes']:>8,} bytes")
    print(f"  Working memory:  {status['working_bytes']:>8,} bytes")
    print(f"  Total:           {status['total_bytes']:>8,} bytes")
    print(f"  Est. tokens:     {status['est_tokens']:>8,}  (threshold: {token_threshold:,})")
    print(f"  Archives:        {status['archives']:>8,} snapshots")
    print()
    print("  Entry counts by tier:")
    for tier, count in counts.items():
        print(f"    [{tier:<10}]  {count}")
    print()
    print(f"  Compaction needed: {'⚠  YES — ' + reason if should else '✓  No — ' + reason}")
    print("═══════════════════════════════════════════════════")


def cmd_read(args, config):
    from src.memory_engine import MemoryEngine
    memory_dir = config.get("memory_dir", "memory")
    engine     = MemoryEngine(memory_dir=memory_dir)

    if args.file == "core":
        print(engine.read_core())
    elif args.file == "working":
        print(engine.read_working())
    else:
        print(engine.read_all())


def cmd_list_archives(args, config):
    from src.memory_engine import MemoryEngine
    memory_dir = config.get("memory_dir", "memory")
    engine     = MemoryEngine(memory_dir=memory_dir)
    archives   = engine.list_archives()

    if not archives:
        print("No archives found.")
        return

    print(f"{'#':<4} {'Filename':<42} {'Label':<18} {'Core':>8} {'Working':>9}")
    print("─" * 85)
    for i, a in enumerate(reversed(archives), 1):
        print(
            f"{i:<4} {a['filename']:<42} {a['label']:<18} "
            f"{a['core_bytes']:>8,} {a['working_bytes']:>9,}"
        )
    print(f"\nTotal: {len(archives)} archives")


def cmd_restore(args, config):
    from src.memory_engine import MemoryEngine
    memory_dir = config.get("memory_dir", "memory")
    engine     = MemoryEngine(memory_dir=memory_dir)

    if not args.yes:
        answer = input(
            f"Restore from '{args.filename}'?\n"
            "Current memory will be archived first, then overwritten. [y/N]: "
        ).strip().lower()
        if answer != "y":
            print("Cancelled.")
            return

    engine.restore_archive(args.filename)
    print(f"✓ Restored from: {args.filename}")
    print("  (Previous state archived as pre_restore snapshot)")


def cmd_clear_working(args, config):
    from src.memory_engine import MemoryEngine, WORKING_TEMPLATE
    memory_dir = config.get("memory_dir", "memory")
    engine     = MemoryEngine(memory_dir=memory_dir)

    if not args.yes:
        answer = input(
            "Clear all working memory (EPISODIC + EPHEMERAL)?\n"
            "Core memory (IDENTITY, PROCEDURAL, SEMANTIC) is unaffected. [y/N]: "
        ).strip().lower()
        if answer != "y":
            print("Cancelled.")
            return

    engine.archive_snapshot("pre_clear_working")
    engine.write_working(WORKING_TEMPLATE)
    print("✓ Working memory cleared. Core memory intact.")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # .env loading is optional

    parser = argparse.ArgumentParser(
        description="Memory Engine — file-based AI context memory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm prompts")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # write
    p = sub.add_parser("write", help="Write a memory entry directly")
    p.add_argument("content", help="Memory text to write")
    p.add_argument(
        "--tier", default="EPISODIC",
        choices=["IDENTITY", "PROCEDURAL", "SEMANTIC", "EPISODIC", "EPHEMERAL"],
        help="Memory tier (default: EPISODIC)",
    )

    # extract
    p = sub.add_parser("extract", help="Extract memories from session context file or stdin")
    p.add_argument("source", help="Path to session log file, or '-' for stdin")

    # compact
    p = sub.add_parser("compact", help="Run compaction cycle")
    p.add_argument("--force", action="store_true", help="Compact even if below threshold")

    # status
    sub.add_parser("status", help="Show memory status and tier counts")

    # read
    p = sub.add_parser("read", help="Print memory file contents")
    p.add_argument("--file", default="all", choices=["core", "working", "all"])

    # list-archives
    sub.add_parser("list-archives", help="List all archive snapshots")

    # restore
    p = sub.add_parser("restore", help="Restore from an archive snapshot")
    p.add_argument("filename", help="Archive filename (from list-archives)")

    # clear-working
    sub.add_parser("clear-working", help="Wipe working memory (keeps core)")

    args   = parser.parse_args()
    config = load_config(args.config)

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "write":         cmd_write,
        "extract":       cmd_extract,
        "compact":       cmd_compact,
        "status":        cmd_status,
        "read":          cmd_read,
        "list-archives": cmd_list_archives,
        "restore":       cmd_restore,
        "clear-working": cmd_clear_working,
    }

    try:
        dispatch[args.command](args, config)
    except KeyboardInterrupt:
        print("\nAborted.")
    except Exception as e:
        print(f"✗ Error: {e}")
        raise


if __name__ == "__main__":
    main()
