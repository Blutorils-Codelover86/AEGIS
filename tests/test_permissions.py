"""Unit tests for deterministic permission policy engine."""
from __future__ import annotations

import unittest

from aegis.audit import InMemoryAuditLogger
from aegis.config import AegisConfig
from aegis.errors import PermissionDenied
from aegis.interfaces import (
    Decision,
    PermissionPolicy,
    PermissionRequest,
    PermissionResult,
)
from aegis.permissions import DeterministicPolicy


class TestDeterministicPolicy(unittest.TestCase):
    """Test suite for DeterministicPolicy enforcing AEGIS safety boundaries."""

    def test_protocol_conformance(self) -> None:
        """Verify DeterministicPolicy satisfies PermissionPolicy protocol."""
        policy = DeterministicPolicy()
        self.assertIsInstance(policy, PermissionPolicy)

    def test_readonly_allowlist_rule(self) -> None:
        """Verify ALLOW for safe read-only actions under allowlist.readonly."""
        policy = DeterministicPolicy()
        readonly_actions = (
            "model.generate",
            "mem.search",
            "mem.list",
            "mem.get",
            "sys.status",
        )
        for action in readonly_actions:
            with self.subTest(action=action):
                req = PermissionRequest(action=action)
                res = policy.evaluate(req)
                self.assertEqual(res.decision, Decision.ALLOW)
                self.assertEqual(res.rule, "allowlist.readonly")

                # require() should succeed and return PermissionResult
                required_res = policy.require(req)
                self.assertEqual(required_res, res)

    def test_shell_deny_by_default(self) -> None:
        """Verify shell.exec is DENY with rule deny.shell by default."""
        policy = DeterministicPolicy()
        req = PermissionRequest(action="shell.exec", parameters={"cmd": "ls -la"})
        res = policy.evaluate(req)
        self.assertEqual(res.decision, Decision.DENY)
        self.assertEqual(res.rule, "deny.shell")

        with self.assertRaises(PermissionDenied) as ctx:
            policy.require(req)
        self.assertEqual(ctx.exception.result.decision, Decision.DENY)
        self.assertEqual(ctx.exception.result.rule, "deny.shell")

    def test_shell_confirm_when_enabled(self) -> None:
        """Verify shell.exec requires confirmation when allow_shell flag is enabled."""
        # Via constructor kwarg
        policy = DeterministicPolicy(allow_shell=True)
        req = PermissionRequest(action="shell.exec", parameters={"cmd": "echo test"})
        res = policy.evaluate(req)
        self.assertEqual(res.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(res.rule, "confirm.shell")

        # Via config dict
        policy_from_dict = DeterministicPolicy({"allow_shell": True})
        res2 = policy_from_dict.evaluate(req)
        self.assertEqual(res2.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(res2.rule, "confirm.shell")

        # require() must still raise PermissionDenied since REQUIRE_CONFIRMATION != ALLOW
        with self.assertRaises(PermissionDenied) as ctx:
            policy.require(req)
        self.assertEqual(ctx.exception.result.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(ctx.exception.result.rule, "confirm.shell")

    def test_fs_read_private_deny_by_default(self) -> None:
        """Verify fs.read_private is DENY with rule deny.private_files by default."""
        policy = DeterministicPolicy()
        req = PermissionRequest(action="fs.read_private", target="~/.ssh/id_rsa")
        res = policy.evaluate(req)
        self.assertEqual(res.decision, Decision.DENY)
        self.assertEqual(res.rule, "deny.private_files")

        with self.assertRaises(PermissionDenied) as ctx:
            policy.require(req)
        self.assertEqual(ctx.exception.result.decision, Decision.DENY)
        self.assertEqual(ctx.exception.result.rule, "deny.private_files")

    def test_fs_read_private_confirm_when_enabled(self) -> None:
        """Verify fs.read_private yields REQUIRE_CONFIRMATION only when explicit flag is on."""
        # Via constructor kwarg
        policy = DeterministicPolicy(allow_private_files=True)
        req = PermissionRequest(action="fs.read_private", target="~/.aws/credentials")
        res = policy.evaluate(req)
        self.assertEqual(res.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(res.rule, "confirm.private_files")

        # Via dict config
        policy_dict = DeterministicPolicy({"allow_private_files": True})
        res2 = policy_dict.evaluate(req)
        self.assertEqual(res2.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(res2.rule, "confirm.private_files")

    def test_net_http_loopback_targets(self) -> None:
        """Verify net.http loopback targets yield ALLOW with rule net.loopback."""
        policy = DeterministicPolicy()
        loopback_targets = [
            "127.0.0.1",
            "localhost",
            "::1",
            "[::1]",
            "http://127.0.0.1:11434/api/tags",
            "http://localhost:8000/status",
            "http://[::1]:9090",
            "127.0.0.1:8080",
            "localhost:5000",
        ]
        for target in loopback_targets:
            with self.subTest(target=target):
                # Target in request.target
                req1 = PermissionRequest(action="net.http", target=target)
                res1 = policy.evaluate(req1)
                self.assertEqual(res1.decision, Decision.ALLOW)
                self.assertEqual(res1.rule, "net.loopback")

                # Target in request.parameters["url"]
                req2 = PermissionRequest(action="net.http", parameters={"url": target})
                res2 = policy.evaluate(req2)
                self.assertEqual(res2.decision, Decision.ALLOW)
                self.assertEqual(res2.rule, "net.loopback")

    def test_net_http_non_loopback_denied_by_default(self) -> None:
        """Verify non-loopback net.http is DENY with rule deny.network by default."""
        policy = DeterministicPolicy()
        external_targets = [
            "http://example.com",
            "https://api.openai.com/v1/models",
            "example.com",
            "192.168.1.1",
            "8.8.8.8",
            "",
            None,
        ]
        for target in external_targets:
            with self.subTest(target=target):
                req = PermissionRequest(action="net.http", target=target)
                res = policy.evaluate(req)
                self.assertEqual(res.decision, Decision.DENY)
                self.assertEqual(res.rule, "deny.network")

                with self.assertRaises(PermissionDenied) as ctx:
                    policy.require(req)
                self.assertEqual(ctx.exception.result.decision, Decision.DENY)
                self.assertEqual(ctx.exception.result.rule, "deny.network")

    def test_net_http_non_loopback_approved_when_allowed(self) -> None:
        """Verify non-loopback net.http yields REQUIRE_CONFIRMATION with confirm.network if allow_network is set."""
        # Via AegisConfig
        cfg = AegisConfig(allow_network=True)
        policy_cfg = DeterministicPolicy(cfg)
        req = PermissionRequest(action="net.http", target="http://example.com/api")
        res = policy_cfg.evaluate(req)
        self.assertEqual(res.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(res.rule, "confirm.network")

        # Via kwarg
        policy_kwarg = DeterministicPolicy(allow_network=True)
        res2 = policy_kwarg.evaluate(req)
        self.assertEqual(res2.decision, Decision.REQUIRE_CONFIRMATION)
        self.assertEqual(res2.rule, "confirm.network")

    def test_high_risk_actions_never_auto_allowed(self) -> None:
        """Verify high-risk actions (shell.exec, fs.read_private, net.http) cannot be auto-allowed."""
        permissive = DeterministicPolicy(
            allow_consequential=True,
            allow_shell=True,
            allow_private_files=True,
            allow_network=True,
        )
        high_risk_requests = [
            PermissionRequest(action="shell.exec", parameters={"cmd": "ls"}),
            PermissionRequest(action="fs.read_private", target="~/.ssh/id_rsa"),
            PermissionRequest(action="net.http", target="http://example.com/api"),
        ]
        for req in high_risk_requests:
            with self.subTest(action=req.action):
                res = permissive.evaluate(req)
                self.assertNotEqual(res.decision, Decision.ALLOW)
                self.assertEqual(res.decision, Decision.REQUIRE_CONFIRMATION)

    def test_consequential_actions_require_confirmation_by_default(self) -> None:
        """Verify consequential actions require confirmation by default."""
        policy = DeterministicPolicy()
        actions = ("fs.write", "mem.save", "mem.delete")
        for action in actions:
            with self.subTest(action=action):
                req = PermissionRequest(action=action, parameters={"data": "test"})
                res = policy.evaluate(req)
                self.assertEqual(res.decision, Decision.REQUIRE_CONFIRMATION)
                self.assertEqual(res.rule, "consequential.confirm_required")

                with self.assertRaises(PermissionDenied) as ctx:
                    policy.require(req)
                self.assertEqual(ctx.exception.result.decision, Decision.REQUIRE_CONFIRMATION)
                self.assertEqual(ctx.exception.result.rule, "consequential.confirm_required")

    def test_consequential_actions_auto_allowed_when_flag_enabled(self) -> None:
        """Verify consequential actions auto-allow under consequential.auto_allowed."""
        # Via AegisConfig
        cfg = AegisConfig(allow_consequential=True)
        policy_cfg = DeterministicPolicy(cfg)
        # Via kwarg
        policy_kwarg = DeterministicPolicy(allow_consequential=True)

        actions = ("fs.write", "mem.save", "mem.delete")
        for policy in (policy_cfg, policy_kwarg):
            for action in actions:
                with self.subTest(policy=policy, action=action):
                    req = PermissionRequest(action=action, parameters={"content": "val"})
                    res = policy.evaluate(req)
                    self.assertEqual(res.decision, Decision.ALLOW)
                    self.assertEqual(res.rule, "consequential.auto_allowed")

                    # require() should succeed
                    req_res = policy.require(req)
                    self.assertEqual(req_res, res)

    def test_unknown_actions_deny_by_default(self) -> None:
        """Verify unknown actions are DENY with rule deny.unknown."""
        policy = DeterministicPolicy()
        unknown_actions = (
            "unknown.action",
            "shell.spawn",
            "exec",
            "random.tool",
            "system.reboot",
            "fs.delete",
            "",
            "mem.purge",
        )
        for action in unknown_actions:
            with self.subTest(action=action):
                req = PermissionRequest(action=action)
                res = policy.evaluate(req)
                self.assertEqual(res.decision, Decision.DENY)
                self.assertEqual(res.rule, "deny.unknown")

                with self.assertRaises(PermissionDenied) as ctx:
                    policy.require(req)
                self.assertEqual(ctx.exception.result.decision, Decision.DENY)
                self.assertEqual(ctx.exception.result.rule, "deny.unknown")

    def test_determinism_repeated_100x(self) -> None:
        """Verify pure determinism: identical request yields identical result 100x."""
        policy = DeterministicPolicy()
        test_requests = [
            PermissionRequest(action="model.generate", parameters={"prompt": "hi"}),
            PermissionRequest(action="shell.exec", parameters={"cmd": "pwd"}),
            PermissionRequest(action="fs.read_private", target="/home/user/.ssh/id_rsa"),
            PermissionRequest(action="net.http", target="http://127.0.0.1:11434"),
            PermissionRequest(action="net.http", target="https://example.com"),
            PermissionRequest(action="mem.save", parameters={"content": "foo"}),
            PermissionRequest(action="bogus.action"),
        ]

        for req in test_requests:
            baseline = policy.evaluate(req)
            for _ in range(100):
                curr = policy.evaluate(req)
                self.assertEqual(curr.decision, baseline.decision)
                self.assertEqual(curr.reason, baseline.reason)
                self.assertEqual(curr.rule, baseline.rule)

    def test_require_audit_logging_when_logger_provided(self) -> None:
        """Verify require() logs audit event when an audit sink is attached."""
        audit = InMemoryAuditLogger()
        policy = DeterministicPolicy(audit=audit)

        # Allowed request
        policy.require(PermissionRequest(action="sys.status"))
        self.assertEqual(len(audit.events), 1)
        self.assertEqual(audit.events[0].event, "permission.eval")
        self.assertEqual(audit.events[0].actor, "policy")
        self.assertEqual(audit.events[0].action, "sys.status")
        self.assertEqual(audit.events[0].decision, "allow")
        self.assertEqual(audit.events[0].details["rule"], "allowlist.readonly")

        # Denied request
        with self.assertRaises(PermissionDenied):
            policy.require(PermissionRequest(action="shell.exec"))
        self.assertEqual(len(audit.events), 2)
        self.assertEqual(audit.events[1].decision, "deny")
        self.assertEqual(audit.events[1].details["rule"], "deny.shell")


if __name__ == "__main__":
    unittest.main()
