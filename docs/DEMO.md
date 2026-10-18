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
Audit Log: /home/blu/.local/share/aegis/audit.jsonl (last event: cli.command at 2026-10-18T13:06:27.861212+00:00)
Stop State: clear

=== Step 3: Local Model Inference ===
Executing single-shot chat prompt against local Ollama model...
Hello!

=== Step 4: Persistent Memory Lifecycle ===
Testing explicit memory save, search, list, and delete...
1. Saving memory...
Saved memory [e966f6b970294aa9b10903a577baa00a]
Extracted Memory Record ID: e966f6b970294aa9b10903a577baa00a
2. Searching memory for 'verified'...
[e966f6b970294aa9b10903a577baa00a] AEGIS Sprint 001 verified ethical local assistant architecture tags=['demo', 'sprint001'] source=demo_script
3. Listing memory entries...
[e966f6b970294aa9b10903a577baa00a] AEGIS Sprint 001 verified ethical local assistant architecture tags=['demo', 'sprint001'] source=demo_script
4. Deleting memory record e966f6b970294aa9b10903a577baa00a...
Deleted memory [e966f6b970294aa9b10903a577baa00a]
5. Verifying record deletion via search...
No memories found.

=== Step 5: Deterministic Permission Denial & Policy Enforcement ===
Running deterministic permission denial and audit demonstration...
=== Step 1: Attempt dangerous shell execution (run_shell) ===
DENIED rule=deny.shell reason=Shell execution is denied by default policy

=== Step 2: Attempt consequential memory save WITHOUT confirmation ===
DENIED rule=consequential.confirm_required reason=Consequential action 'mem.save' requires confirmation
DENIED (bare confirm=True rejected) rule=consequential.confirm_required

=== Step 3: Attempt consequential memory save WITH ConfirmationToken ===
Issued ConfirmationToken (bound fingerprint=d36e8ef782f8df3c...)
SUCCESS: memory saved with id=5c84770ba042419b95001382519a254e content='Authorized memory write with confirmation'

=== Step 4: Structured Audit Log Records ===
{"event":"tool.request","actor":"agent","action":"shell.exec","decision":"deny","outcome":null,"details":{"tool":"run_shell","rule":"deny.shell","reason":"Shell execution is denied by default policy","params":{"cmd":"rm -rf /"},"confirm":false},"timestamp":"2026-10-18T13:06:31.860701+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=33]","tags":["unconfirmed"]},"confirm":false},"timestamp":"2026-10-18T13:06:31.861059+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=33]","tags":["unconfirmed"]},"confirm":true},"timestamp":"2026-10-18T13:06:31.861260+00:00"}
{"event":"tool.request","actor":"agent","action":"mem.save","decision":"require_confirmation","outcome":null,"details":{"tool":"save_memory","rule":"consequential.confirm_required","reason":"Consequential action 'mem.save' requires confirmation","params":{"content":"[REDACTED len=41]","tags":["confirmed"]},"confirm":"d36e8ef782f8df3c68c9b8dd154adb1934a45e04fa5fe865cee1c45157bd82d2"},"timestamp":"2026-10-18T13:06:31.861540+00:00"}
{"event":"tool.result","actor":"agent","action":"mem.save","decision":null,"outcome":"success","details":{"tool":"save_memory"},"timestamp":"2026-10-18T13:06:31.862016+00:00"}

=== Step 6: System Audit Log Tail ===
Audit Log File: /home/blu/.local/share/aegis/audit.jsonl
Tail of last 5 audit entries:
{"event":"cli.command","actor":"user","action":"memory.save","decision":null,"outcome":null,"details":{"argv":["memory","save","[REDACTED len=62]","--tag","demo","sprint001","--source","demo_script"]},"timestamp":"2026-10-18T13:06:29.988096+00:00"}
{"event":"cli.command","actor":"user","action":"memory.search","decision":null,"outcome":null,"details":{"argv":["memory","search","verified"]},"timestamp":"2026-10-18T13:06:30.421720+00:00"}
{"event":"cli.command","actor":"user","action":"memory.list","decision":null,"outcome":null,"details":{"argv":["memory","list","--limit","5"]},"timestamp":"2026-10-18T13:06:30.821614+00:00"}
{"event":"cli.command","actor":"user","action":"memory.delete","decision":null,"outcome":null,"details":{"argv":["memory","delete","e966f6b970294aa9b10903a577baa00a"]},"timestamp":"2026-10-18T13:06:31.206781+00:00"}
{"event":"cli.command","actor":"user","action":"memory.search","decision":null,"outcome":null,"details":{"argv":["memory","search","e966f6b970294aa9b10903a577baa00a"]},"timestamp":"2026-10-18T13:06:31.578793+00:00"}

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
Ran 93 tests in 0.537s

OK
```

All 93 tests passed cleanly across:
- `tests/test_audit.py` (Structured JSON Lines audit logger, 0600 file permissions, prompt/memory/credential redaction)
- `tests/test_config.py` (AegisConfig loading, TOML overrides, environment overrides)
- `tests/test_stop.py` (Emergency stop controller, check/clear signals)
- `tests/test_memory.py` (SQLiteMemoryStore persistence, decoded tag search, punctuation query handling, count(), limit capping)
- `tests/test_cli.py` (CLI argument parser, status, memory, chat, stop subcommands, single-shot Ctrl-C exit code 130, in-flight stop interruption, memory non-persistence in chat)
- `tests/test_provider.py` (Ollama provider client, health checks, error handling, protocol adherence, non-loopback host rejection, redirect blocking)
- `tests/test_permissions.py` (DeterministicPolicy rules, allowlist, denial, confirmation, high-risk action non-bypassability)
- `tests/test_tools.py` (ToolRegistry execution, ConfirmationToken binding, bare confirm=True rejection, conflicting target/url rejection, 302 redirect blocking)

---

## 3. Definition of Done (DoD) Checklist

| Requirement | Status | Proving Evidence / Output |
|---|---|---|
| **1. Starts Locally** | **PASSED** | `.venv` created, `pip install -e .` succeeds with 0 external dependencies (stdlib only). `aegis --help` and `aegis status` run cleanly. |
| **2. Connects to Ollama** | **PASSED** | `aegis status` reports `Ollama Host: http://127.0.0.1:11434`, `Provider Health: True`, and queries `Available Models: ['Ultron-v1.1.0:1.5b', 'qwen2.5-coder:3b', 'qwen2.5-coder:1.5b']`. |
| **3. Answers a Prompt** | **PASSED** | `aegis chat --prompt "Say hello in one short sentence."` calls the local `qwen2.5-coder:1.5b` model and outputs `Hello!`. |
| **4. Persists Authorized Memory** | **PASSED** | Explicit memory lifecycle demonstrated: `aegis memory save` created record `e966f6b970294aa9b10903a577baa00a`, `aegis memory search` found it with tags and metadata, `aegis memory list` listed it, `aegis memory delete` removed it, and subsequent search confirmed `No memories found.` |
| **5. Demonstrates Enforced Permission Denial** | **PASSED** | `scripts/demo_permission_denial.py` demonstrated:<br>1. `run_shell` rejected with `DENIED rule=deny.shell reason=Shell execution is denied by default policy`<br>2. Unconfirmed `save_memory` rejected with `DENIED rule=consequential.confirm_required`<br>3. Bare caller `confirm=True` rejected with `DENIED (bare confirm=True rejected)`<br>4. Cryptographically bound `ConfirmationToken` verified and authorized<br>5. Structured JSONL audit records verified with parameters and sensitive texts redacted. |
| **6. Gated Network Boundary** | **PASSED** | Non-loopback Ollama hosts rejected by default; loopback HTTP redirect (302) to remote blocked; conflicting target/url parameters rejected with `ToolError`. |

*Note: 100% of required steps executed successfully. Zero steps were blocked or skipped.*
