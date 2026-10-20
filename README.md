# Project AEGIS

Local-first, ethical AI agent prototype.

AEGIS provides a local AI assistant designed from the ground up for strict privacy, deterministic safety boundaries, and operator transparency. It runs entirely on local infrastructure with zero runtime external dependencies.

---

## Installation

### Requirements
- Python `>= 3.12`
- Local [Ollama](https://ollama.ai) instance running at `http://127.0.0.1:11434` (for model inference)

### Setup
Install AEGIS in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

To install development or optional full-screen TUI dependencies:

```bash
pip install -e ".[dev]"       # Development and test tools
pip install -e ".[tui]"       # Optional full-screen Textual TUI
pip install -e ".[tui,dev]"   # Complete installation
```

---

## Quickstart & Demo

Stream a rich, styled markdown chat response from your local model:

```bash
aegis chat --prompt "Show a markdown table of 2 rows"
```

Launch the full-screen interactive Textual TUI (requires `[tui]` extra):

```bash
aegis chat --tui
```

Keybindings in TUI:
- `Ctrl+C` / `Ctrl+Q`: Quit
- `Ctrl+L`: Clear history
- `Enter`: Submit message

Run plain, unstyled output suitable for shell pipes and scripts:

```bash
aegis chat --plain --prompt "List 3 colors"
```

For full terminal features, styling tokens, accessibility flags, and streaming indicator details, see [docs/TERMINAL.md](docs/TERMINAL.md).

Run the automated end-to-end demonstrations:

```bash
# Terminal core demonstration (Sprint 002)
bash scripts/demo_terminal.sh

# Sprint 001 architecture & safety demonstration
bash scripts/demo_sprint001.sh
```

Or run the deterministic permission denial demonstration directly (no LLM required):

```bash
python3 scripts/demo_permission_denial.py
```

See [docs/DEMO.md](docs/DEMO.md) for full output transcripts, reproduction logs, and the Definition of Done (DoD) verification checklist.

---

## CLI Overview

AEGIS exposes an intuitive command-line interface:

```bash
# Chat with the local model (single-shot or interactive REPL with real streaming)
aegis chat [--prompt "Your question"] [--model MODEL] [--system "System prompt"] [--plain] [--no-color] [--tui] [--no-memory] [--max-turns 20]

# Check health of local services, provider status, and memory stats
aegis status [--plain] [--no-color]

# Save explicit knowledge to the SQLite memory store
aegis memory save "Important note or memory" [--tag TAG ...] [--source SOURCE] [--plain]

# Search saved memories
aegis memory search "query" [--limit 5] [--plain]

# List stored memories
aegis memory list [--limit 50] [--plain]

# Delete a specific memory record
aegis memory delete RECORD_ID [--plain]

# Signal an emergency stop or clear an existing stop flag
aegis stop [--clear] [--plain]
```

---

## Safety Architecture

AEGIS enforces seven core safety requirements at the architectural level:

1. **Deny-by-Default Permissions**: Any unrecognized or unpermitted action is automatically rejected.
2. **Loopback-Only Network**: Network requests outside local loopback (`127.0.0.1`) are denied by default.
3. **No Private-File Access**: Access to private credential directories (`~/.ssh`, `~/.aws`, `.env`, etc.) is strictly forbidden.
4. **No Self-Modification**: The agent cannot alter its own source code, rules, or audit logs.
5. **Confirmation for Consequential Actions**: Actions with external side effects require interactive operator confirmation.
6. **Stop Mechanism**: An out-of-band `STOP` signal immediately halts ongoing operations.
7. **Worktree Confinement**: Operations are sandboxed to the active project directory.

See [docs/SAFETY.md](docs/SAFETY.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for complete details.

---

## Testing

All tests use standard library `unittest` and are fully compatible with `pytest`. Run the test suite:

```bash
python3 -m unittest discover -s tests -t . -v
```
