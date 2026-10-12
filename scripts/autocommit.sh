#!/bin/bash
# Smart auto-commit: generates descriptive messages from staged changes
cd "$(dirname "$0")/.."

git add -A

# Check if there's anything to commit
if git diff --cached --quiet; then
    echo "Nothing to commit"
    exit 0
fi

# Detect what changed
FILES=$(git diff --cached --name-only)
ADDED=$(echo "$FILES" | grep -v "^$" | wc -l | tr -d ' ')

# Detect type from file paths
TYPE="chore"
echo "$FILES" | grep -q "^tests/" && TYPE="test"
echo "$FILES" | grep -q "^analytics/" && TYPE="feat"
echo "$FILES" | grep -q "^aggregator/" && TYPE="feat"
echo "$FILES" | grep -q "^outreach/" && TYPE="feat"
echo "$FILES" | grep -q "^scripts/" && TYPE="feat"
echo "$FILES" | grep -q "README" && TYPE="docs"
echo "$FILES" | grep -q "ci.yml" && TYPE="ci"
echo "$FILES" | grep -qi "fix" && TYPE="fix"

# Build scope from top-level directories
SCOPE=$(echo "$FILES" | sed 's|/.*||' | sort -u | head -3 | tr '\n' ',' | sed 's/,$//')

# Build description from changed filenames (no paths)
CHANGED=$(echo "$FILES" | sed 's|.*/||' | sort -u | head -5 | tr '\n' ', ' | sed 's/,$//')

MSG="${TYPE}(${SCOPE}): ${CHANGED} (${ADDED} files)"

echo "Committing: $MSG"
git commit -m "$MSG"
git push

