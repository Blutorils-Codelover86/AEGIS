# Project AEGIS - Sprint 001 Demonstration Run & Verification

This document records the exact commands and actual observed output from the Sprint 001 end-to-end integration and security hardening run, followed by the Definition of Done (DoD) verification checklist.

---

## 1. Automated End-to-End Demo Script

The demonstration was executed using the reproducible script [`scripts/demo_sprint001.sh`](../scripts/demo_sprint001.sh):

```bash
bash scripts/demo_sprint001.sh
```

### Actual Observed Console Output

```text
======================================================================
Project AEGIS - Sprint 001 Reproducible End-to-End Demonstration
Repository Root: /home/blu/.ao/data/worktrees/aegis/aegis-7
======================================================================

=== Step 1: Environment & Installation ===
Ensuring virtual environment exists and installing aegis in editable mode...
aegis binary: /home/blu/.ao/data/worktrees/aegis/aegis-7/.venv/bin/aegis
aegis version / entry point verified.

=== Step 2: System Status ===
Checking AEGIS status, Ollama provider health, and local store states...
Model: qwen2.5-coder:1.5b
Ollama Host: http://127.0.0.1:11434
Provider Health: True
Available Models: ['Ultron-v1.1.0:1.5b', 'qwen2.5-coder:3b', 'qwen2.5-coder:1.5b']
Memory DB: /home/blu/.local/share/aegis/memory.db (0 records)
Audit Log: /home/blu/.local/share/aegis/audit.jsonl (last event: cli.command at 2026-10-18T13:37:09.739268+00:00)
Stop State: clear

=== Step 3: Local Model Inference ===
Executing single-shot chat prompt against local Ollama model...
Hello! How can I assist you today?

=== Step 4: Persistent Memory Lifecycle ===
Testing explicit memory save, search, list, and delete...
1. Saving memory...
Saved memory [fc06ccbace294e72bcebee8ac4eaa1b1]
Extracted Memory Record ID: fc06ccbace294e72bcebee8ac4eaa1b1
2. Searching memory for 'verified'...
[fc06ccbace294e72bcebee8ac4eaa1b1] AEGIS Sprint 001 verified ethical local assistant architecture tags=['demo', 'sprint001'] source=demo_script
3. Listing memory entries...
[fc06ccbace294e72bcebee8ac4eaa1b1] AEGIS Sprint 001 verified ethical local assistant architecture tags=['demo', 'sprint001'] source=demo_script
4. Deleting memory record fc06ccbace294e72bcebee8ac4eaa1b1...
Deleted memory [fc06ccbace294e72bcebee8ac4eaa1b1]
5. Verifying record deletion via search...
No memories found.

=== Step 5: Deterministic Permission Denial & Policy Enforcement ===
Running deterministic permission denial and audit demonstration...
=== Step 1: Attempt dangerous shell execution (run_shell) ===
DENIED rule=deny.shell reason=Shell execution is denied by default policy

=== Step 2: Attempt consequential memory save WITHOUT confirmation ===
DENIED rule=consequential.confirm_required reason=Action 'mem.save' requires trusted approval: token missing, forged, replayed, or unapproved
DENIED (bare confirm=True rejected) rule=consequential.confirm_required

=== Step 3: Attempt consequential memory save WITH ConfirmationToken ===
DENIED (forged token rejected by authority)
Issued single-use ConfirmationToken (token_id=f1075007dd6e7431..., bound=d36e8ef782f8df3c...)
SUCCESS: memory saved with id=b24fc68a11194e149ef07a47f5b95903 content='Authorized memory write with confirmation'
DENIED (replayed token rejected: single-use token already consumed)

=== Step 4: Structured Audit Log Records ===
{"event":"tool.request","actor":"agent","action":"shell.exec","decision":"deny","outcome":null,"details":{"tool":"run_shell","rule":"deny.shell","reason":"Shell execution is denied by default policy","params":{"cmd":"[REDACTED len=8]"},"confirm":false},"timestamp":"2026-10-18T13:37:15.642113+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=33]","tags":["[REDACTED tag]"]},"confirm":false},"timestamp":"2026-10-18T13:37:15.642435+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=33]","tags":["[REDACTED tag]"]},"confirm":true},"timestamp":"2026-10-18T13:37:15.642633+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=41]","tags":["[REDACTED tag]"]},"confirm":"forged-token-id-... (bound=forged-fingerpri...)"},"timestamp":"2026-10-18T13:37:15.642896+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=41]","tags":["[REDACTED tag]"]},"confirm":"f1075007dd6e7431... (bound=d36e8ef782f8df3c...)"},"timestamp":"2026-10-18T13:37:15.643124+00:00"}
{"event":"tool.result","actor":"agent","action":"mem.save","decision":null,"outcome":"success","details":{"tool":"save_memory"},"timestamp":"2026-10-18T13:37:15.643478+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=41]","tags":["[REDACTED tag]"]},"confirm":"f1075007dd6e7431... (bound=d36e8ef782f8df3c...)"},"timestamp":"2026-10-18T13:37:15.643636+00:00"}

=== Step 6: System Audit Log Tail ===
Audit Log File: /home/blu/.local/share/aegis/audit.jsonl
Tail of last 5 audit entries:
{"event":"cli.command","actor":"user","action":"memory.save","decision":null,"outcome":null,"details":{"argv":["memory","save","[REDACTED len=62]","--tag","[REDACTED tag]","[REDACTED len=9]","--source","[REDACTED source]"]},"timestamp":"2026-10-18T13:37:13.979616+00:00"}
{"event":"cli.command","actor":"user","action":"memory.search","decision":null,"outcome":null,"details":{"argv":["memory","search","[REDACTED query]"]},"timestamp":"2026-10-18T13:37:14.385311+00:00"}
{"event":"cli.command","actor":"user","action":"memory.list","decision":null,"outcome":null,"details":{"argv":["memory","list","--limit","5"]},"timestamp":"2026-10-18T13:37:14.767715+00:00"}
{"event":"cli.command","actor":"user","action":"memory.delete","decision":null,"outcome":null,"details":{"argv":["memory","delete","[REDACTED id]"]},"timestamp":"2026-10-18T13:37:15.099588+00:00"}
{"event":"cli.command","actor":"user","action":"memory.search","decision":null,"outcome":null,"details":{"argv":["memory","search","[REDACTED query]"]},"timestamp":"2026-10-18T13:37:15.388290+00:00"}

======================================================================
Project AEGIS - Sprint 001 Demonstration Complete (All Steps Passed)
======================================================================
```

---

## 2. Test Suite Execution Output

The integrated unit test suite was executed warning-free under `-W error::ResourceWarning`:

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -t . -v
```

```text
Ran 103 tests in 1.933s

OK
```

All 103 tests passed cleanly across:
- `tests/test_audit.py` (Structured JSON Lines audit logger, 0600 file permissions, strict metadata allowlist, URL query/credential redaction, tags/sources/IDs/values masking, scrubbed error strings)
- `tests/test_config.py` (AegisConfig loading, TOML overrides, environment overrides)
- `tests/test_stop.py` (Emergency stop controller, check/clear signals)
- `tests/test_memory.py` (SQLiteMemoryStore persistence, decoded tag search, punctuation query handling, count(), limit capping)
- `tests/test_cli.py` (CLI argument parser, status, memory, chat, stop subcommands, single-shot Ctrl-C exit code 130, in-flight stop interruption, clean AegisError handling, memory count display)
- `tests/test_provider.py` (Ollama provider client, health checks, error handling, protocol adherence, non-loopback host rejection, redirect blocking, scheme enforcement, stalled stream stop interruption polling via select)
- `tests/test_permissions.py` (DeterministicPolicy rules, allowlist, denial, confirmation, high-risk action non-bypassability, http/https scheme enforcement)
- `tests/test_tools.py` (ToolRegistry execution, ApprovalAuthority integration, single-use token consumption, forgery rejection, replay rejection, conflicting target/url rejection, 302 redirect blocking, non-http scheme rejection)

---

## 3. Definition of Done (DoD) Checklist

| Requirement | Status | Proving Evidence / Output |
|---|---|---|
| **1. Starts Locally** | **PASSED** | `.venv` created, `pip install -e .` succeeds with 0 external dependencies (stdlib only). `aegis --help` and `aegis status` run cleanly. |
| **2. Connects to Ollama** | **PASSED** | `aegis status` reports `Ollama Host: http://127.0.0.1:11434`, `Provider Health: True`, and queries `Available Models: ['Ultron-v1.1.0:1.5b', 'qwen2.5-coder:3b', 'qwen2.5-coder:1.5b']`. |
| **3. Answers a Prompt** | **PASSED** | `aegis chat --prompt "Say hello in one short sentence."` calls the local `qwen2.5-coder:1.5b` model and outputs `Hello! How can I assist you today?`. |
| **4. Persists Authorized Memory** | **PASSED** | Explicit memory lifecycle demonstrated: `aegis memory save` created record `fc06ccbace294e72bcebee8ac4eaa1b1`, `aegis memory search` found it with tags and metadata, `aegis memory list` listed it, `aegis memory delete` removed it, and subsequent search confirmed `No memories found.` |
| **5. Demonstrates Enforced Permission Denial & Single-Use Approval** | **PASSED** | `scripts/demo_permission_denial.py` demonstrated:<br>1. `run_shell` rejected with `DENIED rule=deny.shell reason=Shell execution is denied by default policy`<br>2. Unconfirmed `save_memory` rejected with `DENIED rule=consequential.confirm_required`<br>3. Bare caller `confirm=True` rejected with `DENIED (bare confirm=True rejected)`<br>4. Forged `ConfirmationToken` rejected by `ApprovalAuthority`<br>5. Legitimate `ConfirmationToken` authorized and executed<br>6. Replayed `ConfirmationToken` rejected (single-use token consumed atomically)<br>7. Structured JSONL audit records verified with strict metadata allowlist, query/credential redaction, and scrubbed error messages. |
| **6. Gated Network Boundary & Scheme Enforcement** | **PASSED** | Non-http/https schemes (`file:`, `ftp:`, `data:`, relative URLs) strictly rejected at policy and registry boundaries; non-loopback Ollama hosts rejected by default; loopback HTTP redirect (302) to remote blocked; conflicting target/url parameters rejected with `ToolError`. |
| **7. Interruptible Stop & Stalled Stream Polling** | **PASSED** | `StopController` interrupts generation within 0.2s even on stalled connections using non-blocking socket select polling; `KeyboardInterrupt` exits cleanly with code 130. |

*Note: 100% of required steps executed successfully. Zero steps were blocked or skipped.*
