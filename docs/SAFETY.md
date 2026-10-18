# Project AEGIS — Safety Architecture & Enforcement

Project AEGIS is designed around strict, non-negotiable safety principles. Safety guarantees are enforced by deterministic code and structural constraints, not by model prompting or heuristics.

---

## The Seven Safety Requirements (Sprint 001 Implementation)

| # | Safety Requirement | Implementation & Enforcement Mechanism |
|---|---|---|
| 1 | **Deny-by-Default Permissions** | Unrecognized actions evaluate to `Decision.DENY`. Only explicitly allowlisted safe read actions (`mem.search`, `mem.list`, `sys.status`, `model.generate`) can proceed automatically. |
| 2 | **Gated Loopback Network** | Network requests to non-loopback hosts are blocked (`allow_network = False` by default). Loopback (`127.0.0.1`, `localhost`, `::1`) is enforced across both the model provider and `fetch_url`. All HTTP redirects (3xx) are blocked at the socket/opener level to prevent loopback-to-remote redirect bypasses. Canonical URLs are validated, rejecting conflicting `target` and `url` parameters. |
| 3 | **Private-File Protection** | `fs.read_private` actions evaluate to `Decision.DENY` (`deny.private_files`). The default tool registry intentionally omits arbitrary filesystem reading tools in Sprint 001, eliminating access to credential stores (`~/.ssh`, `~/.aws`, `.env`). |
| 4 | **No Self-Modification** | The agent cannot alter its own source code, permission rules, or configuration. No self-modification tools exist in the default registry. |
| 5 | **ConfirmationToken Enforcement** | Consequential actions (`mem.save`, `mem.delete`, etc.) evaluate to `Decision.REQUIRE_CONFIRMATION`. Bare caller-supplied booleans (`confirm=True`) are rejected. Execution strictly requires a trusted `ConfirmationToken` whose SHA-256 fingerprint cryptographically binds to the exact `PermissionRequest`. High-risk actions (`shell.exec`, `fs.read_private`, non-loopback `net.http`) can NEVER be auto-allowed by global config flags. |
| 6 | **Interruptible Stop Mechanism** | An out-of-band stop file (`STOP`) and `StopController.check()` halt execution unconditionally. Model generation is streamed with periodic stop checks, and `KeyboardInterrupt` (Ctrl+C) in both single-shot and interactive modes cleanly exits with code 130. |
| 7 | **Safe Tool Surface & Worktree Confinement** | The default tool registry restricts capabilities to sandboxed memory operations, safe echo, disabled inert shell (`run_shell` raises `ToolError`), and boundary-checked URL fetching. Arbitrary filesystem mutation tools are excluded from the default registry in Sprint 001. |

---

## Detailed Enforcement Specifications

### 1. Deny-by-Default Permissions
- **Principle**: The absence of an explicit permission grant is always a denial.
- **Enforcement**: `DeterministicPolicy.evaluate(request)` inspects incoming `PermissionRequest` objects. If no rule explicitly matches the requested `action` and `target`, the policy returns `Decision.DENY` with rule `deny.unknown`.
- **Zero Ambiguity**: Safe actions (`mem.search`, `mem.list`, `sys.status`, `model.generate`) are explicitly enumerated. All other actions require justification or are barred.

### 2. Gated Loopback Network & Redirect Blocking
- **Principle**: The agent must not exfiltrate data, communicate with unauthorized remote hosts, or follow redirects past network boundaries.
- **Enforcement**:
  - `AegisConfig.allow_network` defaults to `False`.
  - `OllamaProvider` validates its host during initialization; non-loopback hosts are rejected with `ProviderError` unless `allow_network=True`.
  - `DeterministicPolicy` evaluates `net.http` targets: loopback addresses (`127.0.0.1`, `localhost`, `::1`) yield `Decision.ALLOW` under rule `net.loopback`. Non-loopback targets yield `Decision.DENY` (`deny.network`), or `Decision.REQUIRE_CONFIRMATION` (`confirm.network`) if `allow_network=True`. Non-loopback network access is high-risk and is never auto-allowed.
  - Parameter validation derives ONE canonical URL and rejects conflicting `target` vs `url` parameters with `ToolError`.
  - Both `OllamaProvider` and `fetch_url` install custom `_NoRedirectHandler` openers that reject 301, 302, 303, 307, and 308 redirects, preventing loopback servers from redirecting off-host.

### 3. Private-File Protection & Safe Surface
- **Principle**: Sensitive user credentials, keys, and private configuration must remain inaccessible.
- **Enforcement**:
  - The permission engine evaluates `fs.read_private` as `Decision.DENY` (`deny.private_files`) by default.
  - To prevent unconfined filesystem access, arbitrary file reader/writer tools are excluded from the Sprint 001 default registry. Memory is persisted strictly through the SQLite memory store.

### 4. No Self-Modification
- **Principle**: The agent cannot alter its own logic, relax its permission rules, or suppress its audit trail.
- **Enforcement**:
  - No code-modification or rule-mutation tools are registered.
  - Tool execution occurs strictly through registered `ToolSpec` definitions gated by the policy engine.

### 5. ConfirmationToken Binding for Consequential Actions
- **Principle**: Potentially destructive or consequential actions must not occur without explicit, tamper-proof human approval.
- **Enforcement**:
  - Actions tagged with `consequential=True` evaluate to `Decision.REQUIRE_CONFIRMATION`.
  - A bare boolean `confirm=True` is explicitly rejected by `ToolRegistry.execute`.
  - Execution requires a `ConfirmationToken` generated via `ConfirmationToken.create(request)` whose `fingerprint` is a SHA-256 hash of `(action, target, canonical_parameters, consequential)`.
  - High-risk actions (`shell.exec`, `fs.read_private`, non-loopback `net.http`) can NEVER be auto-allowed by `allow_consequential=True`.
  - The default `run_shell` handler is disabled and raises `ToolError` even if a valid confirmation token is supplied.

### 6. Interruptible Stop Mechanism
- **Principle**: Operators must be able to halt agent execution immediately, including during in-flight generation.
- **Enforcement**:
  - `StopController` manages a local filesystem flag (`<data_dir>/STOP`).
  - `aegis stop` creates the file with a UTC timestamp; `aegis stop --clear` clears it.
  - In-flight model generation is streamed line-by-line while polling `stop_ctrl.check()`. If the stop flag appears, generation halts immediately with `StopRequested`.
  - In both single-shot and interactive REPL chat modes, `KeyboardInterrupt` (Ctrl+C) is caught cleanly and exits with code 130 without stack traces.

### 7. Structured Redacted Audit Logging
- **Principle**: All actions, decisions, and outcomes must be recorded for accountability without leaking sensitive user text.
- **Enforcement**:
  - Audit logs are written in compact JSON Lines format.
  - The audit log file is opened with restrictive `0600` permissions (`-rw-------`).
  - CLI arguments (`--prompt`, `--system`, `memory save <text>`) and tool parameters (`content`, `prompt`, `message`, `text`, and URL credentials) are automatically sanitized and replaced with metadata lengths (`[REDACTED len=N]`) or redacted markers before writing to disk.
