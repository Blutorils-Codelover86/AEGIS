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


def _is_loopback(target: str | None) -> bool:
    """Check deterministically whether target resolves to a loopback address.

    Loopback targets: 127.0.0.1, localhost, ::1.
    """
    if not target or not isinstance(target, str):
        return False
    raw = target.strip()
    if not raw:
        return False

    loopback_hosts = {"127.0.0.1", "localhost", "::1"}

    # Exact hostname match (stripping brackets if IPv6)
    cleaned = raw.removeprefix("[").removesuffix("]")
    if cleaned.lower() in loopback_hosts:
        return True

    url_to_parse = raw
    if "://" not in raw and not raw.startswith("//"):
        url_to_parse = f"http://{raw}"

    try:
        parsed = urllib.parse.urlsplit(url_to_parse)
        hostname = parsed.hostname
        if hostname:
            hostname = hostname.strip().lower()
            if hostname in loopback_hosts:
                return True
    except Exception:
        pass

    return False


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
                target = (
                    request.parameters.get("url")
                    or request.parameters.get("target")
                    or request.parameters.get("host")
                )

            if _is_loopback(str(target) if target is not None else None):
                return PermissionResult(
                    decision=Decision.ALLOW,
                    reason="Loopback network target allowed",
                    rule="net.loopback",
                )
            if self.allow_network:
                return PermissionResult(
                    decision=Decision.ALLOW,
                    reason="Non-loopback network access approved by configuration",
                    rule="net.approved",
                )
            return PermissionResult(
                decision=Decision.DENY,
                reason="Non-loopback network access denied by default policy",
                rule="deny.network",
            )

        # 5. Consequential actions
        if action in self.CONSEQUENTIAL_ACTIONS:
            if self.allow_consequential:
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
