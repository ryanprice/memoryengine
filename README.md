# Memory Engine

File-based AI context memory with tiered compaction. Works with any LLM.

Implements the compaction architecture described by Anthropic's applied AI team:
structured note-taking, tiered decay, and a two-prompt extraction/compaction pipeline
that preserves durable knowledge while shedding noise.

---

## Concepts

Memory is split into **two files** and **five tiers**:

| Tier | File | Durability | Description |
|---|---|---|---|
| `[IDENTITY]` | `core.md` | Permanent | Who the user/system is |
| `[PROCEDURAL]` | `core.md` | Permanent | How to do things — rules, preferences |
| `[SEMANTIC]` | `core.md` | Until contradicted | What is true — facts, decisions |
| `[EPISODIC]` | `working.md` | Decays | What happened — session events |
| `[EPHEMERAL]` | `working.md` | Single session | Transient — raw outputs, lookups |

Compaction **promotes** durable knowledge upward (episodic → semantic → core)
and **discards** noise downward. It never blindly deletes — it asks whether
something can be recovered before removing it.

---

## Quickstart

```bash
git clone <repo>
cd memoryengine

pip install -r requirements.txt
# Then install your provider SDK:
pip install anthropic      # for Claude
pip install openai         # for OpenAI / compatible endpoints

cp .env.example .env
# Edit .env — add your API key

# Edit config.yaml — set provider + model
```

### First run

```bash
python session.py status
```

---

## Configuration

Edit `config.yaml`:

```yaml
provider: openai          # anthropic | openai | ollama | openai_compatible
model: gpt-4o-mini
api_key:                  # or set OPENAI_API_KEY env var

token_threshold: 6000     # trigger compaction above this estimated token count
size_threshold_bytes: 40000
```

For Ollama (local, no API key needed):

```yaml
provider: ollama
model: llama3.2
base_url: http://localhost:11434
```

For any OpenAI-compatible endpoint (Together, Groq, LM Studio, etc.):

```yaml
provider: openai_compatible
model: meta-llama/Llama-3-70b-chat-hf
base_url: https://api.together.xyz/v1
api_key: your_together_key
```

---

## Commands

### Write a memory directly

```bash
python session.py write "User prefers functional over OOP" --tier PROCEDURAL
python session.py write "Project uses Postgres 16 on Supabase" --tier SEMANTIC
python session.py write "Refactored auth module today" --tier EPISODIC
```

### Extract memories from a session log

```bash
# From a file
python session.py extract session_log.txt

# From stdin
cat conversation.txt | python session.py extract -

# Auto-apply without prompt
python session.py extract session_log.txt --yes
```

### Compact memory

```bash
# Auto — checks thresholds first, prompts if below
python session.py compact

# Force — skip threshold check
python session.py compact --force

# Non-interactive (for scripts/cron)
python session.py compact --force --yes
```

### Read memory

```bash
python session.py read              # all
python session.py read --file core
python session.py read --file working
```

### Status

```bash
python session.py status
```

Output:
```
═══ Memory Engine Status ══════════════════════════
  Provider:        openai/gpt-4o-mini
  Core memory:      1,842 bytes
  Working memory:   3,201 bytes
  Est. tokens:      1,260  (threshold: 6,000)
  Archives:             4 snapshots

  Entry counts by tier:
    [IDENTITY  ]  2
    [PROCEDURAL]  3
    [SEMANTIC  ]  8
    [EPISODIC  ]  12
    [EPHEMERAL ]  0

  Compaction needed: ✓  No — within limits
═══════════════════════════════════════════════════
```

### Archive management

```bash
python session.py list-archives
python session.py restore 2024-03-07_120000_pre_compact.md
```

### Clear working memory

```bash
python session.py clear-working   # keeps core.md intact
```

---

## How Compaction Works

Compaction runs a **two-prompt pipeline**:

**1. Compaction pass** — LLM compresses `working.md`:
- Deletes all `[EPHEMERAL]` entries
- Summarises clusters of `[EPISODIC]` entries into higher-level insights
- Converts recurring patterns into `[SEMANTIC]` facts

**2. Merge pass** — LLM merges promoted `[SEMANTIC]` and `[PROCEDURAL]`
entries into `core.md`, resolves contradictions, and deduplicates.

Before every compaction, the current state is archived.
After compaction, the result is archived. You always have two snapshots
bracketing any destructive operation.

---

## Triggers

Compaction is triggered when **either** threshold is exceeded:

| Trigger | Default | Config key |
|---|---|---|
| Estimated token count | 6,000 | `token_threshold` |
| Working file size | 40,000 bytes | `size_threshold_bytes` |

The write command checks thresholds after every write and warns you.
You can also add a cron job for fully automated compaction:

```bash
# Compact daily at midnight if needed
0 0 * * * cd /path/to/memoryengine && python session.py compact --yes
```

---

## Git Strategy

The `.gitignore` is pre-configured to:

- **Track** `core.md` — your durable domain knowledge (version it like code)
- **Ignore** `working.md` — active session state, changes constantly
- **Ignore** `memory/archive/` — large, not useful in version history

To also track archives (optional):
```bash
# In .gitignore, comment out:
# memory/archive/
```

---

## Programmatic Usage

```python
from src.memory_engine import MemoryEngine
from src.providers import get_provider
from src.compactor import Compactor

engine    = MemoryEngine(memory_dir="memory")
provider  = get_provider({"provider": "anthropic", "model": "claude-opus-4-5-20251101"})
compactor = Compactor(engine, provider)

# Write
engine.append_memory("User is building a SaaS product", tier="IDENTITY")

# Extract from session
extracted = compactor.extract(session_text, auto_apply=True)

# Check and compact
should, reason = compactor.should_compact(config)
if should:
    stats = compactor.compact()
    print(f"Reduced by {stats['reduction']['tokens']} tokens")
```

---

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Tests use a `MockProvider` — no API calls, no cost.

---

## File Structure

```
memoryengine/
├── session.py          CLI entry point
├── config.yaml         Configuration
├── requirements.txt
├── .env.example
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── providers.py    LLM abstraction (Anthropic, OpenAI, Ollama, compatible)
│   ├── memory_engine.py File I/O, tiering, archiving, token estimation
│   └── compactor.py    Two-prompt extraction + compaction pipeline
├── memory/
│   ├── core.md         IDENTITY + PROCEDURAL + SEMANTIC (commit this)
│   └── archive/        Point-in-time snapshots + index.json
└── tests/
    └── test_engine.py
```
