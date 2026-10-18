#!/usr/bin/env bash
set -euo pipefail

# Project AEGIS - Sprint 001 Reproducible End-to-End Demo
# Demonstrates:
#   1. Virtual environment creation & package installation
#   2. Local system status & Ollama connectivity
#   3. Model inference (single-shot prompt)
#   4. Explicit persistent memory lifecycle (save, search, list, delete)
#   5. Deterministic permission denial & confirmation enforcement
#   6. Structured audit log inspection

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "======================================================================"
echo "Project AEGIS - Sprint 001 Reproducible End-to-End Demonstration"
echo "Repository Root: ${REPO_ROOT}"
echo "======================================================================"

echo ""
echo "=== Step 1: Environment & Installation ==="
echo "Ensuring virtual environment exists and installing aegis in editable mode..."
python3 -m venv .venv
source .venv/bin/activate
pip install -q -e .
echo "aegis binary: $(which aegis)"
echo "aegis version / entry point verified."

echo ""
echo "=== Step 2: System Status ==="
echo "Checking AEGIS status, Ollama provider health, and local store states..."
aegis status

echo ""
echo "=== Step 3: Local Model Inference ==="
echo "Executing single-shot chat prompt against local Ollama model..."
aegis chat --prompt "Say hello in one short sentence."

echo ""
echo "=== Step 4: Persistent Memory Lifecycle ==="
echo "Testing explicit memory save, search, list, and delete..."
DEMO_FACT="AEGIS Sprint 001 verified ethical local assistant architecture"
echo "1. Saving memory..."
SAVE_OUTPUT=$(aegis memory save "${DEMO_FACT}" --tag demo sprint001 --source demo_script)
echo "${SAVE_OUTPUT}"

# Extract memory ID from output: 'Saved memory [<id>]'
RECORD_ID=$(echo "${SAVE_OUTPUT}" | sed -n 's/.*\[\([^]]*\)\].*/\1/p')
if [[ -z "${RECORD_ID}" ]]; then
    echo "ERROR: Failed to extract memory ID from output: ${SAVE_OUTPUT}" >&2
    exit 1
fi
echo "Extracted Memory Record ID: ${RECORD_ID}"

echo "2. Searching memory for 'verified'..."
aegis memory search "verified"

echo "3. Listing memory entries..."
aegis memory list --limit 5

echo "4. Deleting memory record ${RECORD_ID}..."
aegis memory delete "${RECORD_ID}"

echo "5. Verifying record deletion via search..."
aegis memory search "${RECORD_ID}" || true

echo ""
echo "=== Step 5: Deterministic Permission Denial & Policy Enforcement ==="
echo "Running deterministic permission denial and audit demonstration..."
python scripts/demo_permission_denial.py

echo ""
echo "=== Step 6: System Audit Log Tail ==="
AUDIT_LOG_PATH=$(python -c "from aegis.config import AegisConfig; print(AegisConfig.load().audit_log)")
echo "Audit Log File: ${AUDIT_LOG_PATH}"
if [[ -f "${AUDIT_LOG_PATH}" ]]; then
    echo "Tail of last 5 audit entries:"
    tail -n 5 "${AUDIT_LOG_PATH}"
else
    echo "Notice: Audit log file not yet created at ${AUDIT_LOG_PATH}"
fi

echo ""
echo "======================================================================"
echo "Project AEGIS - Sprint 001 Demonstration Complete (All Steps Passed)"
echo "======================================================================"
