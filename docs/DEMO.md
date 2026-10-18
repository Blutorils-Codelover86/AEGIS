# Project AEGIS - Sprint 001 Demonstration Run & Verification

This document records the exact commands and actual observed output from the Sprint 001 end-to-end integration run, followed by the Definition of Done (DoD) verification checklist.

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
Audit Log: /home/blu/.local/share/aegis/audit.jsonl (last event: cli.command at 2026-10-18T12:45:09.203374+00:00)
Stop State: clear

=== Step 3: Local Model Inference ===
Executing single-shot chat prompt against local Ollama model...
Hello!

=== Step 4: Persistent Memory Lifecycle ===
Testing explicit memory save, search, list, and delete...
1. Saving memory...
Saved memory [74277942c41f49558f65e58efa3f1e35]
Extracted Memory Record ID: 74277942c41f49558f65e58efa3f1e35
2. Searching memory for 'verified'...
[74277942c41f49558f65e58efa3f1e35] AEGIS Sprint 001 verified ethical local assistant architecture tags=['demo', 'sprint001'] source=demo_script
3. Listing memory entries...
[74277942c41f49558f65e58efa3f1e35] AEGIS Sprint 001 verified ethical local assistant architecture tags=['demo', 'sprint001'] source=demo_script
4. Deleting memory record 74277942c41f49558f65e58efa3f1e35...
Deleted memory [74277942c41f49558f65e58efa3f1e35]
5. Verifying record deletion via search...
No memories found.

=== Step 5: Deterministic Permission Denial & Policy Enforcement ===
Running deterministic permission denial and audit demonstration...
=== Step 1: Attempt dangerous shell execution (run_shell) ===
DENIED rule=deny.shell reason=Shell execution is denied by default policy

=== Step 2: Attempt consequential memory save WITHOUT confirmation ===
DENIED rule=consequential.confirm_required reason=Consequential action 'mem.save' requires confirmation

=== Step 3: Attempt consequential memory save WITH confirmation ===
SUCCESS: memory saved with id=887e91a4cd9f4a4faee9fe7469be8668 content='Authorized memory write with confirmation'

=== Step 4: Structured Audit Log Records ===
{"event":"tool.request","actor":"agent","action":"shell.exec","decision":"deny","outcome":null,"details":{"tool":"run_shell","rule":"deny.shell","reason":"Shell execution is denied by default policy","params":{"cmd":"rm -rf /"},"confirm":false},"timestamp":"2026-10-18T12:45:12.170888+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"Unauthorized memory write attempt","tags":["unconfirmed"]},"confirm":false},"timestamp":"2026-10-18T12:45:12.171256+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"Authorized memory write with confirmation","tags":["confirmed"]},"confirm":true},"timestamp":"2026-10-18T12:45:12.171449+00:00"}
{"event":"tool.result","actor":"agent","action":"mem.save","decision":null,"outcome":"success","details":{"tool":"save_memory"},"timestamp":"2026-10-18T12:45:12.171900+00:00"}

=== Step 6: System Audit Log Tail ===
Audit Log File: /home/blu/.local/share/aegis/audit.jsonl
Tail of last 5 audit entries:
{"event":"cli.command","actor":"user","action":"memory.save","decision":null,"outcome":null,"details":{"argv":["memory","save","AEGIS Sprint 001 verified ethical local assistant architecture","--tag","demo","sprint001","--source","demo_script"]},"timestamp":"2026-10-18T12:45:10.670232+00:00"}
{"event":"cli.command","actor":"user","action":"memory.search","decision":null,"outcome":null,"details":{"argv":["memory","search","verified"]},"timestamp":"2026-10-18T12:45:11.068511+00:00"}
{"event":"cli.command","actor":"user","action":"memory.list","decision":null,"outcome":null,"details":{"argv":["memory","list","--limit","5"]},"timestamp":"2026-10-18T12:45:11.328781+00:00"}
{"event":"cli.command","actor":"user","action":"memory.delete","decision":null,"outcome":null,"details":{"argv":["memory","delete","74277942c41f49558f65e58efa3f1e35"]},"timestamp":"2026-10-18T12:45:11.640500+00:00"}
{"event":"cli.command","actor":"user","action":"memory.search","decision":null,"outcome":null,"details":{"argv":["memory","search","74277942c41f49558f65e58efa3f1e35"]},"timestamp":"2026-10-18T12:45:11.925137+00:00"}

======================================================================
Project AEGIS - Sprint 001 Demonstration Complete (All Steps Passed)
======================================================================
```

---

## 2. Test Suite Execution Output

The integrated unit test suite was executed across all components:

```bash
python3 -m unittest discover -s tests -t . -v
```

```text
Ran 80 tests in 0.339s

OK
```

All 80 tests passed across:
- `tests/test_audit.py` (Structured JSON Lines audit logger, event serialization)
- `tests/test_config.py` (AegisConfig loading, TOML overrides, environment overrides)
- `tests/test_stop.py` (Emergency stop controller, check/clear signals)
- `tests/test_memory.py` (SQLiteMemoryStore persistence, search, list, delete, concurrency)
- `tests/test_cli.py` (CLI argument parser, status, memory, chat, stop subcommands)
- `tests/test_provider.py` (Ollama provider client, health checks, error handling, protocol adherence)
- `tests/test_permissions.py` (DeterministicPolicy rules, allowlist, denial, confirmation)
- `tests/test_tools.py` (ToolRegistry execution, audit hook, permission gating)

---

## 3. Definition of Done (DoD) Checklist

| Requirement | Status | Proving Evidence / Output |
|---|---|---|
| **1. Starts Locally** | **PASSED** | `.venv` created, `pip install -e .` succeeds with 0 external dependencies (stdlib only). `aegis --help` and `aegis status` run cleanly. |
| **2. Connects to Ollama** | **PASSED** | `aegis status` reports `Ollama Host: http://127.0.0.1:11434`, `Provider Health: True`, and queries `Available Models: ['Ultron-v1.1.0:1.5b', 'qwen2.5-coder:3b', 'qwen2.5-coder:1.5b']`. |
| **3. Answers a Prompt** | **PASSED** | `aegis chat --prompt "Say hello in one short sentence."` calls the local `qwen2.5-coder:1.5b` model and outputs `Hello!`. |
| **4. Persists Authorized Memory** | **PASSED** | Explicit memory lifecycle demonstrated: `aegis memory save` created record `74277942c41f49558f65e58efa3f1e35`, `aegis memory search` found it with tags and metadata, `aegis memory list` listed it, `aegis memory delete` removed it, and subsequent search confirmed `No memories found.` |
| **5. Demonstrates Enforced Permission Denial** | **PASSED** | `scripts/demo_permission_denial.py` demonstrated:<br>1. `run_shell` rejected with `DENIED rule=deny.shell reason=Shell execution is denied by default policy`<br>2. Unconfirmed `save_memory` rejected with `DENIED rule=consequential.confirm_required reason=Consequential action 'mem.save' requires confirmation`<br>3. Confirmed `save_memory` succeeded with `confirm=True`<br>4. Structured JSONL audit records verified for every evaluation and execution. |

*Note: 100% of required steps executed successfully. Zero steps were blocked or skipped.*
