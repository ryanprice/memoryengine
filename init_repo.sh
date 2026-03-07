#!/usr/bin/env bash
# Run once to initialise the git repo and make first commit
set -e

echo "Initialising memory engine repo..."

git init
git add session.py config.yaml requirements.txt README.md .env.example .gitignore
git add src/
git add tests/
git add memory/

# Create empty archive gitkeep
mkdir -p memory/archive
touch memory/archive/.gitkeep
git add memory/archive/.gitkeep

git commit -m "feat: initial memory engine

- File-based AI context memory with five-tier system
- Two-prompt extraction + compaction pipeline
- Multi-provider support: Anthropic, OpenAI, Ollama, OpenAI-compatible
- Automatic archiving before/after every compaction
- CLI: write, extract, compact, status, restore, clear-working
- Triggers: token threshold, file size threshold, manual/forced"

echo ""
echo "✓ Git repo initialised."
echo ""
echo "Next steps:"
echo "  1. Create repo on GitHub/GitLab"
echo "  2. git remote add origin <url>"
echo "  3. git push -u origin main"
