# Project AEGIS — Safety Architecture & Enforcement

Project AEGIS is designed around seven non-negotiable safety principles. Safety guarantees are enforced by deterministic code and structural constraints, not by model prompting or heuristics.

---

## The Seven Safety Requirements

| # | Safety Requirement | Design Enforcement Mechanism |
|---|---|---|
| 1 | **Deny-by-Default Permissions** | Unrecognized actions always evaluate to `Decision.DENY`. Only explicitly allowlisted actions can proceed. |
| 2 | **Loopback-Only Network** | Network requests to non-loopback addresses are blocked (`allow_network = False` by default). Only local Ollama (`127.0.0.1`) is permitted. |
| 3 | **No Private-File Access** | Strict path inspection blocks access to sensitive directories (`~/.ssh`, `~/.aws`, `~/.gnupg`, `.env`, private keys). |
| 4 | **No Self-Modification** | The agent is forbidden from editing its own source files, permission rules, configuration, or audit logs. |
| 5 | **Confirmation for Consequential Actions** | Actions with persistent side effects (`shell.exec`, `fs.write`, `mem.delete`, etc.) yield `REQUIRE_CONFIRMATION`. |
| 6 | **Stop Mechanism** | An out-of-band stop file (`STOP`) and `StopController.check()` halt execution unconditionally when triggered. |
| 7 | **Worktree Confinement** | File system operations and tool executions are strictly confined within the designated workspace/worktree root. |

---

## Detailed Enforcement Specifications

### 1. Deny-by-Default Permissions
- **Principle**: The absence of an explicit permission grant is always a denial.
- **Enforcement**: `PermissionPolicy.evaluate(request)` inspects incoming `PermissionRequest` objects. If no rule explicitly matches the requested `action` and `target`, the policy returns `Decision.DENY` with an explanatory rule name.
- **Zero Ambiguity**: Safe actions (`mem.search`, `mem.list`, `sys.status`, `model.generate`) are explicitly enumerated. All other actions require justification or are barred.

### 2. Loopback-Only Network
- **Principle**: The agent must not exfiltrate data or fetch untrusted remote content.
- **Enforcement**:
  - `AegisConfig.allow_network` defaults to `False`.
  - The model provider interface connects exclusively to `http://127.0.0.1:11434` (Ollama).
  - Any network request (`net.http`) targeting non-loopback IP addresses or remote hostnames evaluates to `Decision.DENY`.

### 3. No Private-File Access
- **Principle**: Sensitive user credentials, keys, and private configuration must remain inaccessible.
- **Enforcement**:
  - The permission engine normalizes target paths and compares them against a blacklist of sensitive paths, including:
    - `~/.ssh/`
    - `~/.aws/`
    - `~/.gnupg/`
    - Shell histories (`.bash_history`, `.zsh_history`)
    - Environment secrets files (`.env`, `*.pem`, `*.key`)
  - Any operation attempting to read, write, or inspect these paths triggers `Decision.DENY` under a dedicated safety rule.

### 4. No Self-Modification
- **Principle**: The agent cannot alter its own logic, relax its permission rules, or suppress its audit trail.
- **Enforcement**:
  - File write requests targeting the `aegis` library source code, configuration files (`aegis.toml`), or the audit log (`audit.jsonl`) are intercepted.
  - The permission policy rejects modification of runtime code with `Decision.DENY`.

### 5. Confirmation for Consequential Actions
- **Principle**: Potentially destructive or irreversible actions must not occur without explicit human approval.
- **Enforcement**:
  - Actions tagged with `consequential=True` (e.g. `shell.exec`, `fs.write`, `fs.delete`, `mem.delete`) evaluate to `Decision.REQUIRE_CONFIRMATION` by default.
  - The CLI blocks execution and prompts the human operator with action details and target parameters.
  - If the operator denies the prompt or `allow_consequential` is `False`, execution is aborted and an audit event is logged.

### 6. Stop Mechanism
- **Principle**: A user or automated monitor must be able to halt agent execution instantly and reliably.
- **Enforcement**:
  - `StopController` manages a local filesystem flag (`<data_dir>/STOP`).
  - Running loops (such as multi-turn chat REPL or batch tasks) invoke `controller.check()` prior to each action and model generation.
  - If the stop flag exists, `check()` raises `StopRequested`, terminating execution without executing pending actions.
  - Operators can trigger this at any time via `aegis stop` or by creating the file directly.

### 7. Worktree Confinement
- **Principle**: Tool operations must stay within the user's project boundary.
- **Enforcement**:
  - Target paths are resolved using `os.path.realpath` to prevent path traversal attacks (`../../`).
  - Any target resolving outside the active worktree root is rejected by the permission engine.
