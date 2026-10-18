"""Tool registry and execution engine for Project AEGIS."""
from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from aegis.errors import AegisError, PermissionDenied, ToolError
from aegis.interfaces import (
    AuditEvent,
    AuditLogger,
    ConfirmationToken,
    Decision,
    MemoryStore,
    PermissionPolicy,
    PermissionRequest,
    PermissionResult,
)
from aegis.permissions.authority import ApprovalAuthority


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that blocks all HTTP redirects to enforce boundaries."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_301(self, req, fp, code, msg, headers):
        raise ToolError(f"HTTP redirect ({code}) blocked by security policy")

    def http_error_302(self, req, fp, code, msg, headers):
        raise ToolError(f"HTTP redirect ({code}) blocked by security policy")

    def http_error_303(self, req, fp, code, msg, headers):
        raise ToolError(f"HTTP redirect ({code}) blocked by security policy")

    def http_error_307(self, req, fp, code, msg, headers):
        raise ToolError(f"HTTP redirect ({code}) blocked by security policy")

    def http_error_308(self, req, fp, code, msg, headers):
        raise ToolError(f"HTTP redirect ({code}) blocked by security policy")


def _redact_url_for_audit(url: str) -> str:
    """Redact user credentials and query strings from URLs for audit logging."""
    try:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username or parsed.password:
            netloc = f"[REDACTED]@{netloc}"
        query = "[REDACTED_QUERY]" if parsed.query else ""
        fragment = "[REDACTED_FRAGMENT]" if parsed.fragment else ""
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
    except Exception:
        return "[REDACTED_URL]"


def _redact_params_for_audit(params: Mapping[str, Any]) -> dict[str, Any]:
    """Strict metadata allowlist for auditing tool parameters.

    Preserves parameter keys while strictly redacting sensitive payloads,
    URL queries/userinfo, tags, sources, record IDs, and arbitrary tool values.
    """
    redacted: dict[str, Any] = {}
    for k, v in params.items():
        k_lower = str(k).lower()
        if k_lower in ("content", "text", "prompt", "message", "secret", "password", "token", "key"):
            if isinstance(v, str):
                redacted[k] = f"[REDACTED len={len(v)}]"
            else:
                redacted[k] = "[REDACTED]"
        elif k_lower in ("url", "target"):
            redacted[k] = _redact_url_for_audit(str(v))
        elif k_lower == "tags":
            if isinstance(v, (list, tuple, set)):
                redacted[k] = ["[REDACTED tag]" for _ in v]
            else:
                redacted[k] = "[REDACTED tags]"
        elif k_lower == "source":
            redacted[k] = "[REDACTED source]"
        elif k_lower in ("id", "record_id"):
            redacted[k] = "[REDACTED id]"
        elif k_lower in ("query", "search"):
            redacted[k] = "[REDACTED query]"
        elif k_lower in ("limit", "timeout", "confirm"):
            redacted[k] = v
        else:
            if isinstance(v, str):
                redacted[k] = f"[REDACTED len={len(v)}]"
            else:
                redacted[k] = "[REDACTED]"
    return redacted


def _default_tool_handler(params: Mapping[str, Any]) -> Any:
    return None


@dataclass
class ToolSpec:
    """Specification of an executable tool."""

    name: str
    description: str
    action: str
    consequential: bool = False
    handler: Callable[[Mapping[str, Any]], Any] = _default_tool_handler


class ToolRegistry:
    """Registry managing available tools and sandboxed execution."""

    def __init__(self, authority: ApprovalAuthority | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.authority = authority

    def register(self, spec: ToolSpec) -> None:
        """Register a tool specification."""
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        """Retrieve a tool specification by name."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Return list of all registered tool names."""
        return list(self._tools.keys())

    def execute(
        self,
        name: str,
        params: Mapping[str, Any] | None = None,
        *,
        policy: PermissionPolicy,
        audit: AuditLogger,
        actor: str = "agent",
        confirm: ConfirmationToken | str | bool | None = None,
        authority: ApprovalAuthority | None = None,
    ) -> Any:
        """Evaluate permission, audit, and execute a registered tool."""
        spec = self.get(name)
        if spec is None:
            raise ToolError(f"Unknown tool: '{name}'")

        raw_parameters: Mapping[str, Any] = params if params is not None else {}
        parameters = dict(raw_parameters)

        # Validate conflicting target and url parameters (B1)
        has_target = "target" in parameters and parameters["target"] is not None
        has_url = "url" in parameters and parameters["url"] is not None
        target: str | None = None

        if has_target and has_url:
            if str(parameters["target"]) != str(parameters["url"]):
                raise ToolError("Conflicting 'target' and 'url' parameters provided")
            target = str(parameters["target"])
        elif has_target:
            target = str(parameters["target"])
        elif has_url:
            target = str(parameters["url"])
        elif "path" in parameters and parameters["path"] is not None:
            target = str(parameters["path"])

        # Enforce scheme and host validity for network actions at tool boundary (B1)
        if spec.action == "net.http" or name == "fetch_url":
            if not target:
                raise ToolError("URL parameter is required")
            try:
                parsed = urllib.parse.urlsplit(target)
            except Exception as e:
                raise ToolError(f"Malformed URL '{target}': {e}") from e
            if parsed.scheme.lower() not in ("http", "https"):
                raise ToolError(
                    f"Invalid URL scheme '{parsed.scheme}': only http and https are permitted"
                )
            if not parsed.hostname:
                raise ToolError(f"Invalid URL '{target}': concrete hostname is required")

        # Pass canonical URL to both target and url in parameters
        if target is not None and (has_target or has_url):
            parameters["target"] = target
            parameters["url"] = target

        request = PermissionRequest(
            action=spec.action,
            target=target,
            parameters=parameters,
            consequential=spec.consequential,
        )

        perm_result = policy.evaluate(request)

        # Audit confirm parameter with sensitive token masking
        confirm_audit: Any = False
        if isinstance(confirm, ConfirmationToken):
            confirm_audit = f"{confirm.token_id[:16]}... (bound={confirm.request_fingerprint[:16]}...)"
        elif isinstance(confirm, str):
            confirm_audit = f"{confirm[:16]}..."
        elif isinstance(confirm, bool):
            confirm_audit = confirm

        # Audit tool request evaluation with strict parameter redaction (before consuming token)
        audit.log(
            AuditEvent(
                event="tool.request",
                actor=actor,
                action=spec.action,
                decision=perm_result.decision.value,
                details={
                    "tool": name,
                    "rule": perm_result.rule,
                    "reason": perm_result.reason,
                    "params": _redact_params_for_audit(parameters),
                    "confirm": confirm_audit,
                },
            )
        )

        if perm_result.decision == Decision.DENY:
            raise PermissionDenied(perm_result)

        # B2: Trusted approval authority validation and atomic consumption
        if perm_result.decision == Decision.REQUIRE_CONFIRMATION:
            effective_authority = authority or self.authority
            confirmed = False
            if effective_authority is not None and confirm is not None and not isinstance(confirm, bool):
                confirmed = effective_authority.consume_token(confirm, request)

            if not confirmed:
                raise PermissionDenied(
                    PermissionResult(
                        decision=Decision.REQUIRE_CONFIRMATION,
                        reason=f"Action '{spec.action}' requires trusted approval: token missing, forged, replayed, or unapproved",
                        rule=perm_result.rule,
                    )
                )

        try:
            result = spec.handler(parameters)
        except Exception as exc:
            error_type = type(exc).__name__
            if isinstance(exc, PermissionDenied):
                scrubbed_msg = f"PermissionDenied: rule={exc.result.rule}"
            elif isinstance(exc, ToolError):
                scrubbed_msg = f"ToolError: execution failed for tool '{name}'"
            else:
                scrubbed_msg = f"{error_type}: execution failed"

            audit.log(
                AuditEvent(
                    event="tool.result",
                    actor=actor,
                    action=spec.action,
                    outcome="error",
                    details={
                        "tool": name,
                        "error": scrubbed_msg,
                        "error_type": error_type,
                    },
                )
            )
            if isinstance(exc, AegisError):
                raise
            raise ToolError(f"Tool '{name}' execution failed: {exc}") from exc

        audit.log(
            AuditEvent(
                event="tool.result",
                actor=actor,
                action=spec.action,
                outcome="success",
                details={
                    "tool": name,
                },
            )
        )
        return result


def default_registry(
    memory: MemoryStore | None = None,
    authority: ApprovalAuthority | None = None,
) -> ToolRegistry:
    """Create a default tool registry with standard system tools."""
    registry = ToolRegistry(authority=authority)

    def _echo_handler(params: Mapping[str, Any]) -> Any:
        if "message" in params:
            return params["message"]
        if "text" in params:
            return params["text"]
        return dict(params)

    def _search_memory_handler(params: Mapping[str, Any]) -> Any:
        if memory is None:
            raise ToolError("Memory store is not available")
        query = str(params.get("query", ""))
        limit = int(params.get("limit", 5))
        return memory.search(query, limit=limit)

    def _save_memory_handler(params: Mapping[str, Any]) -> Any:
        if memory is None:
            raise ToolError("Memory store is not available")
        content = str(params.get("content", ""))
        tags = params.get("tags", ())
        source = params.get("source")
        return memory.save(content, tags=tags, source=source)

    def _delete_memory_handler(params: Mapping[str, Any]) -> Any:
        if memory is None:
            raise ToolError("Memory store is not available")
        record_id = str(params.get("record_id") or params.get("id") or "")
        return memory.delete(record_id)

    def _run_shell_handler(params: Mapping[str, Any]) -> Any:
        raise ToolError("shell execution is disabled")

    def _fetch_url_handler(params: Mapping[str, Any]) -> Any:
        url = str(params.get("url") or params.get("target") or "").strip()
        if not url:
            raise ToolError("URL parameter is required")
        try:
            parsed = urllib.parse.urlsplit(url)
        except Exception as exc:
            raise ToolError(f"Malformed URL '{url}': {exc}") from exc
        if parsed.scheme.lower() not in ("http", "https"):
            raise ToolError(f"Invalid URL scheme '{parsed.scheme}': only http and https are permitted")
        if not parsed.hostname:
            raise ToolError(f"Invalid URL '{url}': concrete hostname is required")

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AEGIS/0.1.0"})
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(req, timeout=5.0) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"HTTP request to '{url}' failed: {exc}") from exc

    registry.register(
        ToolSpec(
            name="echo",
            description="Echo back input parameters or text safely",
            action="sys.status",
            consequential=False,
            handler=_echo_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="search_memory",
            description="Search persistent memory for relevant records",
            action="mem.search",
            consequential=False,
            handler=_search_memory_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="save_memory",
            description="Save a new record to persistent memory",
            action="mem.save",
            consequential=True,
            handler=_save_memory_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="delete_memory",
            description="Delete a record from persistent memory by ID",
            action="mem.delete",
            consequential=True,
            handler=_delete_memory_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="run_shell",
            description="Execute a shell command (disabled by safety architecture)",
            action="shell.exec",
            consequential=True,
            handler=_run_shell_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_url",
            description="Fetch content from an approved HTTP/HTTPS URL",
            action="net.http",
            consequential=True,
            handler=_fetch_url_handler,
        )
    )

    return registry
