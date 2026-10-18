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

To install development dependencies (e.g. pytest):

```bash
pip install -e ".[dev]"
```

---

## CLI Overview

AEGIS exposes an intuitive command-line interface:

```bash
# Chat with the local model (single-shot or interactive REPL)
aegis chat [--prompt "Your question"] [--model MODEL] [--system "System prompt"] [--no-memory] [--max-turns 20]

# Check health of local services, provider status, and memory stats
aegis status

# Save explicit knowledge to the SQLite memory store
aegis memory save "Important note or memory" [--tag TAG ...] [--source SOURCE]

# Search saved memories
aegis memory search "query" [--limit 5]

# List stored memories
aegis memory list [--limit 50]

# Delete a specific memory record
aegis memory delete RECORD_ID

# Signal an emergency stop or clear an existing stop flag
aegis stop [--clear]
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
