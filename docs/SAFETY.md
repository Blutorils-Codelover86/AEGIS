# Project AEGIS — Safety Architecture & Enforcement

Project AEGIS is designed around strict, non-negotiable safety principles. Safety guarantees are enforced by deterministic code and structural constraints, not by model prompting or heuristics.

---

## The Seven Safety Requirements (Sprint 001 Implementation)

| # | Safety Requirement | Implementation & Enforcement Mechanism |
|---|---|---|
| 1 | **Deny-by-Default Permissions** | Unrecognized actions evaluate to `Decision.DENY`. Only explicitly allowlisted safe read actions (`mem.search`, `mem.list`, `sys.status`, `model.generate`) can proceed automatically. |
| 2 | **Gated Loopback Network & Scheme Enforcement** | Network access requires an absolute `http` or `https` scheme and a concrete host at both policy and tool boundaries. Non-HTTP schemes (`file:`, `ftp:`, `data:`, relative URLs) are rejected. Requests to non-loopback hosts are blocked by default (`allow_network = False`). Loopback (`127.0.0.1`, `localhost`, `::1`) is enforced across both the model provider and `fetch_url`. All HTTP redirects (3xx) are blocked at the socket/opener level. Canonical URLs are validated, rejecting conflicting `target` and `url` parameters. |
| 3 | **Private-File Protection** | `fs.read_private` actions evaluate to `Decision.DENY` (`deny.private_files`). The default tool registry intentionally omits arbitrary filesystem reading tools in Sprint 001, eliminating access to credential stores (`~/.ssh`, `~/.aws`, `.env`). Attempting to use `fetch_url` with `file://` is rejected at the scheme validation boundary. |
| 4 | **No Self-Modification** | The agent cannot alter its own source code, permission rules, or configuration. No self-modification tools exist in the default registry. |
| 5 | **Trusted Single-Use Approval Authority** | Consequential actions (`mem.save`, `mem.delete`, etc.) evaluate to `Decision.REQUIRE_CONFIRMATION`. Bare caller-supplied booleans (`confirm=True`) are rejected. Execution strictly requires an opaque, session-bound `ConfirmationToken` issued solely by a runtime-owned `ApprovalAuthority` and atomically consumed on execution. Replayed and forged tokens are rejected. High-risk actions (`shell.exec`, `fs.read_private`, non-loopback `net.http`) can NEVER be auto-allowed by global config flags. |
| 6 | **Interruptible Stop Mechanism & Stalled Polling** | An out-of-band stop file (`STOP`) and `StopController.check()` halt execution unconditionally. Model generation is streamed with non-blocking socket polling (`select`) every 0.1s so that stop requests interrupt even stalled network streams without waiting for timeouts. `KeyboardInterrupt` (Ctrl+C) in both single-shot and interactive modes cleanly exits with code 130. |
| 7 | **Safe Tool Surface & Worktree Confinement** | The default tool registry restricts capabilities to sandboxed memory operations, safe echo, disabled inert shell (`run_shell` raises `ToolError`), and scheme-validated loopback URL fetching. Arbitrary filesystem mutation tools are excluded from the default registry in Sprint 001. |

---

## Detailed Enforcement Specifications

### 1. Deny-by-Default Permissions
- **Principle**: The absence of an explicit permission grant is always a denial.
- **Enforcement**: `DeterministicPolicy.evaluate(request)` inspects incoming `PermissionRequest` objects. If no rule explicitly matches the requested `action` and `target`, the policy returns `Decision.DENY` with rule `deny.unknown`.
- **Zero Ambiguity**: Safe actions (`mem.search`, `mem.list`, `sys.status`, `model.generate`) are explicitly enumerated. All other actions require justification or are barred.

### 2. Gated Loopback Network, Scheme Validation & Redirect Blocking
- **Principle**: The agent must not read local files via URL parsers, exfiltrate data, communicate with unauthorized remote hosts, or follow redirects past network boundaries.
- **Enforcement**:
  - `AegisConfig.allow_network` defaults to `False`.
  - Both `DeterministicPolicy` and `ToolRegistry` validate URL schemes: only absolute `http` and `https` schemes with concrete hostnames are accepted. `file:`, `ftp:`, `data:`, scheme-relative (`//`), and local file paths are rejected before execution.
  - `OllamaProvider` validates its host during initialization; non-loopback hosts are rejected with `ProviderError` unless `allow_network=True`.
  - Loopback addresses (`127.0.0.1`, `localhost`, `::1`) yield `Decision.ALLOW` under rule `net.loopback`. Non-loopback targets yield `Decision.DENY` (`deny.network`), or `Decision.REQUIRE_CONFIRMATION` (`confirm.network`) if `allow_network=True`. Non-loopback network access is high-risk and is never auto-allowed.
  - Parameter validation derives ONE canonical URL and rejects conflicting `target` vs `url` parameters with `ToolError`.
  - Both `OllamaProvider` and `fetch_url` install custom `_NoRedirectHandler` openers that reject 301, 302, 303, 307, and 308 redirects, preventing loopback servers from redirecting off-host.
- **Trust Boundary Note**: In Sprint 001, loopback checking is performed on the configured hostname/IP strings; external proxy resolution and DNS rebinding protections are deferred to future sprints.

### 3. Private-File Protection & Safe Surface
- **Principle**: Sensitive user credentials, keys, and private configuration must remain inaccessible.
- **Enforcement**:
  - The permission engine evaluates `fs.read_private` as `Decision.DENY` (`deny.private_files`) by default.
  - Arbitrary file reader/writer tools are excluded from the Sprint 001 default registry.
  - Local file reading via `fetch_url` (e.g. `file://localhost/etc/hosts`) is strictly barred by scheme validation.

### 4. No Self-Modification
- **Principle**: The agent cannot alter its own logic, relax its permission rules, or suppress its audit trail.
- **Enforcement**:
  - No code-modification or rule-mutation tools are registered.
  - Tool execution occurs strictly through registered `ToolSpec` definitions gated by the policy engine.

### 5. Trusted Single-Use Approval Authority
- **Principle**: Potentially destructive or consequential actions must not occur without explicit, tamper-proof human approval.
- **Enforcement**:
  - Actions tagged with `consequential=True` evaluate to `Decision.REQUIRE_CONFIRMATION`.
  - A bare boolean `confirm=True` is explicitly rejected by `ToolRegistry.execute`.
  - Public/model-accessible token constructors (`ConfirmationToken.create`) are eliminated.
  - Tokens are minted solely by an `ApprovalAuthority` owned by the CLI/runtime. Tokens are opaque, bound to a specific session and SHA-256 request fingerprint.
  - `ToolRegistry.execute` atomically consumes the token via `authority.consume_token()`. Replayed tokens or tokens forged without authority registration fail with `PermissionDenied`.
  - High-risk actions (`shell.exec`, `fs.read_private`, non-loopback `net.http`) can NEVER be auto-allowed by `allow_consequential=True`.
  - The default `run_shell` handler is disabled and raises `ToolError` even if a valid confirmation token is supplied.
- **Trust Boundary Note**: The `ApprovalAuthority` lives within the host process memory and relies on process-level isolation between the CLI orchestrator and model execution contexts.

### 6. Interruptible Stop Mechanism & Stalled Polling
- **Principle**: Operators must be able to halt agent execution immediately, including during in-flight generation or stalled network streams.
- **Enforcement**:
  - `StopController` manages a local filesystem flag (`<data_dir>/STOP`).
  - `aegis stop` creates the file with a UTC timestamp; `aegis stop --clear` clears it.
  - In-flight model generation uses an actively cancellable background worker during the initial HTTP response header-wait phase, and non-blocking socket select polling (`select.select([fn], [], [], 0.1)`) during stream read. Both poll `stop_ctrl.check()` every 100ms. If the stop flag appears, generation aborts immediately with `StopRequested`, rather than blocking on the 120s HTTP timeout.
  - In both single-shot and interactive REPL chat modes, `KeyboardInterrupt` (Ctrl+C) is caught cleanly and exits with code 130 without stack traces.

### 7. Structured Redacted Audit Logging
- **Principle**: All actions, decisions, and outcomes must be recorded for accountability without leaking sensitive user text or credentials.
- **Enforcement**:
  - Audit logs are written in compact JSON Lines format.
  - The audit log file is opened with restrictive `0600` permissions (`-rw-------`).
  - Strict metadata allowlist: parsed CLI argument names and option structures are logged, while prompts (`--prompt`, `--system`), memory content (`memory save`), search queries, record IDs, and sources are redacted.
  - Tool parameters redact raw payload text, tags, sources, IDs, and URL query secrets (`?[REDACTED_QUERY]`) / userinfo credentials (`[REDACTED]@host`).
  - Audited error messages are scrubbed to prevent leaking parameter content in exception strings.
