"""Deterministic permission policy engine for Project AEGIS."""
from __future__ import annotations

import urllib.parse
from typing import Any, Mapping

from aegis.config import AegisConfig
from aegis.errors import PermissionDenied
from aegis.interfaces import (
    AuditEvent,
    AuditLogger,
    Decision,
    PermissionPolicy,
    PermissionRequest,
    PermissionResult,
)


def _validate_http_target(target: str | None) -> tuple[bool, str | None, bool]:
    """Validate target for net.http actions.

    Returns (is_valid, hostname, is_loopback).
    Rejects any scheme other than http/https (e.g. file:, ftp:, data:),
    empty hostnames, relative paths without host, or unparseable URLs.
    """
    if not target or not isinstance(target, str):
        return False, None, False
    raw = target.strip()
    if not raw or raw.startswith("/"):
        return False, None, False

    loopback_hosts = {"127.0.0.1", "localhost", "::1"}

    # If target has a scheme with ://
    if "://" in raw:
        try:
            parsed = urllib.parse.urlsplit(raw)
        except Exception:
            return False, None, False

        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return False, None, False

        hostname = parsed.hostname
        if not hostname:
            return False, None, False

        hostname = hostname.strip().lower()
        return True, hostname, hostname in loopback_hosts

    # If target starts with // (scheme-relative) -> reject, absolute http/https required
    if raw.startswith("//"):
        return False, None, False

    # Target without :// - check if it contains a colon (could be IPv6, host:port, or scheme:data)
    if ":" in raw:
        # IPv6 address: e.g. [::1], ::1, [::1]:8080
        if raw.startswith("[") or raw == "::1" or raw.count(":") >= 2:
            cleaned = raw.split("]")[0].removeprefix("[").strip().lower()
            return True, cleaned, cleaned in loopback_hosts
        # Single colon: host:port OR non-http scheme like data:foo, mailto:foo
        host_part, _, rest = raw.partition(":")
        port_or_rest = rest.split("/")[0]
        if port_or_rest.isdigit():
            cleaned = host_part.strip().lower()
            if not cleaned:
                return False, None, False
            return True, cleaned, cleaned in loopback_hosts
        # Not a port -> non-http scheme, reject
        return False, None, False

    # Bare hostname or IP without port (e.g. localhost, 127.0.0.1, example.com)
    cleaned = raw.split("/")[0].strip().lower()
    if not cleaned:
        return False, None, False
    return True, cleaned, cleaned in loopback_hosts


def _is_loopback(target: str | None) -> bool:
    """Check deterministically whether target resolves to a loopback address.

    Loopback targets: 127.0.0.1, localhost, ::1 under http/https schemes.
    """
    is_valid, _, is_loopback = _validate_http_target(target)
    return is_valid and is_loopback


class DeterministicPolicy:
    """Deterministic, explainable permission checker implementing PermissionPolicy.

    Pure function of (request, config); stable rule IDs; no randomness; no hidden state.
    """

    ALLOWED_READONLY: frozenset[str] = frozenset({
        "model.generate",
        "mem.search",
        "mem.list",
        "mem.get",
        "sys.status",
    })

    CONSEQUENTIAL_ACTIONS: frozenset[str] = frozenset({
        "fs.write",
        "mem.save",
        "mem.delete",
    })

    def __init__(
        self,
        config: AegisConfig | Mapping[str, Any] | None = None,
        *,
        allow_shell: bool = False,
        allow_private_files: bool = False,
        allow_network: bool | None = None,
        allow_consequential: bool | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        cfg_network = False
        cfg_consequential = False
        cfg_shell = False
        cfg_private = False

        if config is not None:
            if isinstance(config, Mapping):
                cfg_network = bool(config.get("allow_network", False))
                cfg_consequential = bool(config.get("allow_consequential", False))
                cfg_shell = bool(config.get("allow_shell", False))
                cfg_private = bool(config.get("allow_private_files", False))
            else:
                cfg_network = bool(getattr(config, "allow_network", False))
                cfg_consequential = bool(getattr(config, "allow_consequential", False))
                cfg_shell = bool(getattr(config, "allow_shell", False))
                cfg_private = bool(getattr(config, "allow_private_files", False))

        self.allow_network: bool = cfg_network if allow_network is None else bool(allow_network)
        self.allow_consequential: bool = (
            cfg_consequential if allow_consequential is None else bool(allow_consequential)
        )
        self.allow_shell: bool = bool(allow_shell) or cfg_shell
        self.allow_private_files: bool = bool(allow_private_files) or cfg_private
        self.config: AegisConfig | Mapping[str, Any] | None = config
        self.audit: AuditLogger | None = audit

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        """Pure evaluation of permission request against policy rules."""
        action = request.action

        # 1. Safe read-only actions
        if action in self.ALLOWED_READONLY:
            return PermissionResult(
                decision=Decision.ALLOW,
                reason=f"Action '{action}' is in safe read-only allowlist",
                rule="allowlist.readonly",
            )

        # 2. Shell execution
        if action == "shell.exec":
            if self.allow_shell:
                return PermissionResult(
                    decision=Decision.REQUIRE_CONFIRMATION,
                    reason="Shell execution requires explicit confirmation",
                    rule="confirm.shell",
                )
            return PermissionResult(
                decision=Decision.DENY,
                reason="Shell execution is denied by default policy",
                rule="deny.shell",
            )

        # 3. Private file access
        if action == "fs.read_private":
            if self.allow_private_files:
                return PermissionResult(
                    decision=Decision.REQUIRE_CONFIRMATION,
                    reason="Private file access requires explicit confirmation",
                    rule="confirm.private_files",
                )
            return PermissionResult(
                decision=Decision.DENY,
                reason="Private file access is denied by default policy",
                rule="deny.private_files",
            )

        # 4. Network HTTP requests
        if action == "net.http":
            target = request.target
            if not target and isinstance(request.parameters, Mapping):
                url_param = request.parameters.get("url")
                tgt_param = request.parameters.get("target")
                if url_param is not None and tgt_param is not None and str(url_param) != str(tgt_param):
                    return PermissionResult(
                        decision=Decision.DENY,
                        reason="Conflicting url and target parameters",
                        rule="deny.conflicting_target",
                    )
                target = url_param or tgt_param or request.parameters.get("host")

            is_valid, hostname, is_loopback = _validate_http_target(str(target) if target is not None else None)
            if not is_valid:
                return PermissionResult(
                    decision=Decision.DENY,
                    reason="Invalid or unsupported network scheme (only http and https with a concrete host are allowed)",
                    rule="deny.network",
                )

            if is_loopback:
                return PermissionResult(
                    decision=Decision.ALLOW,
                    reason="Loopback network target allowed",
                    rule="net.loopback",
                )
            if self.allow_network:
                # Non-loopback network is high-risk: requires confirmation even when network is enabled
                return PermissionResult(
                    decision=Decision.REQUIRE_CONFIRMATION,
                    reason="Non-loopback network access approved by configuration requires confirmation",
                    rule="confirm.network",
                )
            return PermissionResult(
                decision=Decision.DENY,
                reason="Non-loopback network access denied by default policy",
                rule="deny.network",
            )

        # 5. Consequential actions
        if action in self.CONSEQUENTIAL_ACTIONS:
            # High-risk actions must NEVER be auto-allowed by global config flags
            high_risk = {"shell.exec", "fs.read_private", "net.http"}
            if self.allow_consequential and action not in high_risk:
                return PermissionResult(
                    decision=Decision.ALLOW,
                    reason=f"Consequential action '{action}' auto-allowed by configuration",
                    rule="consequential.auto_allowed",
                )
            return PermissionResult(
                decision=Decision.REQUIRE_CONFIRMATION,
                reason=f"Consequential action '{action}' requires confirmation",
                rule="consequential.confirm_required",
            )

        # 6. Any other/unknown action
        return PermissionResult(
            decision=Decision.DENY,
            reason=f"Action '{action}' is unrecognized and denied by default",
            rule="deny.unknown",
        )

    def require(self, request: PermissionRequest) -> PermissionResult:
        """Evaluate request and raise PermissionDenied if decision is not ALLOW."""
        result = self.evaluate(request)
        if self.audit is not None:
            self.audit.log(
                AuditEvent(
                    event="permission.eval",
                    actor="policy",
                    action=request.action,
                    decision=result.decision.value,
                    details={
                        "rule": result.rule,
                        "reason": result.reason,
                        "target": request.target,
                    },
                )
            )
        if result.decision != Decision.ALLOW:
            raise PermissionDenied(result)
        return result
