#!/usr/bin/env python3
"""Deterministic demo of AEGIS permission denial, confirmation token binding, and policy enforcement.

No LLM model or external services required.
Demonstrates:
1. Deny-by-default on dangerous actions (run_shell -> PermissionDenied).
2. Requirement of ConfirmationToken for consequential actions (save_memory -> PermissionDenied without token or with bare confirm=True).
3. Successful execution when confirmed via trusted ConfirmationToken bound to the request.
4. Structured JSON Lines audit logging of all decisions and outcomes with sensitive content redaction.
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
from aegis.interfaces import ConfirmationToken, Decision, PermissionRequest
from aegis.memory import SQLiteMemoryStore
from aegis.permissions import ApprovalAuthority, DeterministicPolicy
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
        mem_params = {"content": "Unauthorized memory write attempt", "tags": ("unconfirmed",)}
        try:
            registry.execute(
                "save_memory",
                mem_params,
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

        # Also prove that bare caller-supplied confirm=True is rejected
        try:
            registry.execute(
                "save_memory",
                mem_params,
                policy=policy,
                audit=audit,
                confirm=True,
            )
            print("FAILURE: bare confirm=True was unexpectedly accepted!", file=sys.stderr)
            return 1
        except PermissionDenied as e:
            print(f"DENIED (bare confirm=True rejected) rule={e.result.rule}")

        # Step C: Attempt consequential action with forged token vs trusted ApprovalAuthority
        print("\n=== Step 3: Attempt consequential memory save WITH ConfirmationToken ===")
        auth_params = {"content": "Authorized memory write with confirmation", "tags": ("confirmed",)}
        auth_request = PermissionRequest(
            action="mem.save",
            parameters=auth_params,
            consequential=True,
        )

        authority = ApprovalAuthority()
        registry.authority = authority

        # 3a. Forged token attempt (model/attacker crafts a fake ConfirmationToken)
        forged_token = ConfirmationToken(
            token_id="forged-token-id-1234567890",
            request_fingerprint="forged-fingerprint",
            session_id=authority.session_id,
            action="mem.save",
        )
        try:
            registry.execute(
                "save_memory",
                auth_params,
                policy=policy,
                audit=audit,
                confirm=forged_token,
            )
            print("FAILURE: forged token was unexpectedly accepted!", file=sys.stderr)
            return 1
        except PermissionDenied:
            print("DENIED (forged token rejected by authority)")

        # 3b. Legitimate single-use token minted by trusted ApprovalAuthority
        token = authority.issue_token(auth_request, comment="Human operator approved")
        print(f"Issued single-use ConfirmationToken (token_id={token.token_id[:16]}..., bound={token.request_fingerprint[:16]}...)")

        record = registry.execute(
            "save_memory",
            auth_params,
            policy=policy,
            audit=audit,
            confirm=token,
        )
        print(f"SUCCESS: memory saved with id={record.id} content='{record.content}'")

        # 3c. Replay attempt with the exact same already-consumed token
        try:
            registry.execute(
                "save_memory",
                auth_params,
                policy=policy,
                audit=audit,
                confirm=token,
            )
            print("FAILURE: replayed token was unexpectedly accepted!", file=sys.stderr)
            return 1
        except PermissionDenied:
            print("DENIED (replayed token rejected: single-use token already consumed)")

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
