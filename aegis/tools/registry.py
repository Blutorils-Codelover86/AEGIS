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
)


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


def _redact_url_credentials(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            netloc = f"[REDACTED]@{netloc}"
            return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        pass
    return url


def _redact_params_for_audit(params: Mapping[str, Any]) -> dict[str, Any]:
    redacted = {}
    sensitive_keys = {"content", "text", "prompt", "message", "secret", "password", "token", "key"}
    for k, v in params.items():
        k_lower = str(k).lower()
        if k_lower in sensitive_keys:
            if isinstance(v, str):
                redacted[k] = f"[REDACTED len={len(v)}]"
            else:
                redacted[k] = "[REDACTED]"
        elif k_lower in {"url", "target"}:
            redacted[k] = _redact_url_credentials(str(v))
        else:
            redacted[k] = v
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

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

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
        confirm: ConfirmationToken | bool | None = None,
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

        # Audit tool request evaluation with parameter and credential redaction (m3)
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
                    "confirm": confirm.fingerprint if isinstance(confirm, ConfirmationToken) else bool(confirm),
                },
            )
        )

        if perm_result.decision == Decision.DENY:
            raise PermissionDenied(perm_result)

        # M1: Confirmation must not be a bare caller-supplied confirm=True.
        # It requires a trusted ConfirmationToken whose fingerprint binds to the exact PermissionRequest.
        if perm_result.decision == Decision.REQUIRE_CONFIRMATION:
            if not isinstance(confirm, ConfirmationToken) or not confirm.is_valid_for(request):
                raise PermissionDenied(perm_result)

        try:
            result = spec.handler(parameters)
        except Exception as exc:
            audit.log(
                AuditEvent(
                    event="tool.result",
                    actor=actor,
                    action=spec.action,
                    outcome="error",
                    details={
                        "tool": name,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
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


def default_registry(memory: MemoryStore | None = None) -> ToolRegistry:
    """Create a default tool registry with standard system tools."""
    registry = ToolRegistry()

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
        url = str(params.get("url") or params.get("target") or "")
        if not url:
            raise ToolError("URL parameter is required")
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
