"""Trusted approval authority for Project AEGIS."""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

from aegis.interfaces import (
    ConfirmationToken,
    PermissionRequest,
    compute_request_fingerprint,
)


class ApprovalAuthority:
    """Trusted approval authority owned by the CLI / operator runtime.

    Mints opaque, single-use ConfirmationTokens bound to a session and canonical
    request fingerprint. Tokens are stored in a private in-memory registry and
    consumed atomically upon execution to prevent replay and forgery.
    """

    def __init__(self, session_id: str | None = None) -> None:
        self._session_id = session_id or secrets.token_hex(16)
        self._lock = threading.Lock()
        self._active_tokens: dict[str, ConfirmationToken] = {}

    @property
    def session_id(self) -> str:
        """The unique session ID of this authority."""
        return self._session_id

    def issue_token(
        self,
        request: PermissionRequest,
        comment: str = "Operator approved",
    ) -> ConfirmationToken:
        """Issue an opaque, single-use ConfirmationToken bound to the canonical request."""
        token_id = secrets.token_hex(32)
        fp = compute_request_fingerprint(request)
        ts = datetime.now(timezone.utc).isoformat()
        token = ConfirmationToken(
            token_id=token_id,
            request_fingerprint=fp,
            session_id=self._session_id,
            action=request.action,
            target=request.target,
            created_at=ts,
            comment=comment,
        )
        with self._lock:
            self._active_tokens[token_id] = token
        return token

    def consume_token(
        self,
        token: ConfirmationToken | str | None,
        request: PermissionRequest,
    ) -> bool:
        """Atomically validate and consume a single-use token.

        Returns True only if the token was issued by this authority for the current
        session and matching request fingerprint, and has not yet been consumed.
        Once consumed, the token is permanently removed and cannot be replayed.
        """
        if token is None or isinstance(token, bool):
            return False

        token_id = token.token_id if isinstance(token, ConfirmationToken) else str(token)
        fp = compute_request_fingerprint(request)

        with self._lock:
            recorded = self._active_tokens.pop(token_id, None)
            if recorded is None:
                # Token not found (forged, unknown, or already consumed/replayed)
                return False

            if recorded.session_id != self._session_id:
                return False
            if recorded.action != request.action:
                return False
            if recorded.request_fingerprint != fp:
                return False
            if isinstance(token, ConfirmationToken):
                if token.session_id != self._session_id or token.request_fingerprint != fp:
                    return False

            return True

    def is_active(self, token_id: str) -> bool:
        """Check if a token ID is currently active and unconsumed."""
        with self._lock:
            return token_id in self._active_tokens
