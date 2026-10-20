#!/usr/bin/env bash
# Project AEGIS - Sprint 002 Terminal Core Demonstration
# Non-destructive demonstration of rich terminal streaming, plain output, and status.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${REPO_ROOT}/.venv/bin/python"

if [ ! -f "${PYTHON}" ]; then
    echo "Virtual environment not found at .venv. Please set up the environment first."
    exit 1
fi

echo "======================================================================"
echo "Project AEGIS - Sprint 002 Rich Terminal Demonstration"
echo "Repository: ${REPO_ROOT}"
echo "======================================================================"

echo ""
echo "=== Step 1: Styled Terminal Streaming (Pseudo-TTY Capture) ==="
script -qec "${PYTHON} -m aegis chat --prompt 'Show a markdown table of 2 rows'" /dev/null

echo ""
echo "=== Step 2: Plain Output (Verified Zero ANSI Escape Codes) ==="
"${PYTHON}" -m aegis chat --plain --prompt "Reply with exactly three bullet points naming colors."

echo ""
echo "=== Step 3: ANSI Escape Verification ==="
if "${PYTHON}" -m aegis chat --plain --prompt "Test" | grep -P '\x1b|\033'; then
    echo "FAIL: Escape sequences found in --plain output!"
    exit 1
else
    echo "SUCCESS: --plain produced zero escape codes."
fi

echo ""
echo "=== Step 4: System Status Display ==="
"${PYTHON}" -m aegis status

echo ""
echo "======================================================================"
echo "Sprint 002 Terminal Demonstration Complete."
echo "======================================================================"
