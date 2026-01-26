#!/bin/bash
# Update Claude stats and push to GitHub
# Run manually or via launchd schedule

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "$(date): Updating Claude stats..."

# Generate fresh stats
python3 parse_claude_stats.py

# Check if there are changes to commit
if git diff --quiet claude_stats.json 2>/dev/null; then
    echo "$(date): No changes to Claude stats"
    exit 0
fi

# Commit and push
git add claude_stats.json
git commit -m "Update Claude stats [skip ci]"
git push

echo "$(date): Claude stats updated and pushed"
