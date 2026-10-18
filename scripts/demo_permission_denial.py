#!/usr/bin/env python3
"""Deterministic demo of AEGIS permission denial and policy enforcement.

No LLM model or external services required.
Demonstrates:
1. Deny-by-default on dangerous actions (run_shell -> PermissionDenied).
2. Requirement of confirmation for consequential actions (save_memory -> PermissionDenied without confirm).
3. Successful execution when confirmed (save_memory -> success with confirm=True).
4. Structured JSON Lines audit logging of all decisions and outcomes.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure aegis is importable if running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aegis.audit import JsonlAuditLogger
from aegis.config import AegisConfig
from aegis.errors import PermissionDenied
from aegis.interfaces import Decision
from aegis.memory import SQLiteMemoryStore
from aegis.permissions import DeterministicPolicy
from aegis.tools import default_registry


def main() -> int:
    # 1. Build DeterministicPolicy with default config
    config = AegisConfig()
    policy = DeterministicPolicy(config)

    # 2. Build JsonlAuditLogger in a temporary file
    temp_dir = tempfile.mkdtemp(prefix="aegis_demo_")
    audit_path = os.path.join(temp_dir, "demo_audit.jsonl")
    db_path = os.path.join(temp_dir, "demo_memory.db")

    audit = JsonlAuditLogger(audit_path)
    store = SQLiteMemoryStore(db_path)
    registry = default_registry(memory=store)

    try:
        # Step A: Attempt dangerous action (run_shell)
        print("=== Step 1: Attempt dangerous shell execution (run_shell) ===")
        try:
            registry.execute(
                "run_shell",
                {"cmd": "rm -rf /"},
                policy=policy,
                audit=audit,
            )
            print("FAILURE: run_shell was unexpectedly permitted!", file=sys.stderr)
            return 1
        except PermissionDenied as e:
            print(f"DENIED rule={e.result.rule} reason={e.result.reason}")

        # Step B: Attempt consequential action WITHOUT confirmation (save_memory)
        print("\n=== Step 2: Attempt consequential memory save WITHOUT confirmation ===")
        try:
            registry.execute(
                "save_memory",
                {"content": "Unauthorized memory write attempt", "tags": ("unconfirmed",)},
                policy=policy,
                audit=audit,
                confirm=False,
            )
            print("FAILURE: unconfirmed save_memory was unexpectedly permitted!", file=sys.stderr)
            return 1
        except PermissionDenied as e:
            if e.result.decision != Decision.REQUIRE_CONFIRMATION:
                print(
                    f"FAILURE: Expected REQUIRE_CONFIRMATION decision, got {e.result.decision}",
                    file=sys.stderr,
                )
                return 1
            print(f"DENIED rule={e.result.rule} reason={e.result.reason}")

        # Step C: Attempt consequential action WITH confirmation (save_memory)
        print("\n=== Step 3: Attempt consequential memory save WITH confirmation ===")
        record = registry.execute(
            "save_memory",
            {"content": "Authorized memory write with confirmation", "tags": ("confirmed",)},
            policy=policy,
            audit=audit,
            confirm=True,
        )
        print(f"SUCCESS: memory saved with id={record.id} content='{record.content}'")

        # Step D: Inspect structured audit records
        audit.close()
        print("\n=== Step 4: Structured Audit Log Records ===")
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                print(line.rstrip())

    finally:
        store.close()
        # Clean up temporary test files
        try:
            if os.path.exists(audit_path):
                os.remove(audit_path)
            if os.path.exists(db_path):
                os.remove(db_path)
            os.rmdir(temp_dir)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
