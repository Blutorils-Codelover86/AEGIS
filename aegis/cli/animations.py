"""Terminal animations and indicators for Project AEGIS."""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping

from rich.console import Console


@dataclass(frozen=True)
class AnimatedCaps:
    """Animation and styling capabilities of the current terminal environment."""

    enabled: bool
    is_tty: bool = False


def capabilities(
    console: Console,
    *,
    plain: bool = False,
    env: Mapping[str, str] | None = None,
) -> AnimatedCaps:
    """Determine whether animated indicators should be enabled.

    Enabled only when stdout is a TTY, plain is False, NO_COLOR is unset,
    and AEGIS_NO_ANIMATION is unset.
    """
    if env is None:
        env = os.environ

    is_tty = bool(getattr(console, "is_terminal", False))
    if not is_tty and hasattr(console.file, "isatty"):
        try:
            is_tty = bool(console.file.isatty())
        except Exception:
            is_tty = False

    is_no_color = getattr(console, "no_color", False) or ("NO_COLOR" in env)
    is_no_anim = "AEGIS_NO_ANIMATION" in env

    enabled = bool(is_tty and not plain and not is_no_color and not is_no_anim)
    return AnimatedCaps(enabled=enabled, is_tty=is_tty)


class ThinkingIndicator:
    """Context manager showing a spinner while awaiting the first chunk."""

    def __init__(
        self,
        console: Console,
        caps: AnimatedCaps,
        message: str = "Thinking...",
    ) -> None:
        self.console = console
        self.caps = caps
        self.message = message
        self._status: Any = None

    def __enter__(self) -> ThinkingIndicator:
        if self.caps.enabled:
            try:
                self._status = self.console.status(
                    f"[aegis.muted]{self.message}[/aegis.muted]",
                    spinner="dots",
                )
                self._status.start()
            except Exception:
                self._status = None
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def stop(self) -> None:
        """Stop and clear the spinner."""
        if self._status is not None:
            try:
                self._status.stop()
            except Exception:
                pass
            finally:
                self._status = None


class StreamIndicator:
    """Subtle live indicator showing streaming progress and token rate without artificial delays."""

    def __init__(self, console: Console, caps: AnimatedCaps) -> None:
        self.console = console
        self.caps = caps
        self._status: Any = None
        self._started = False

    def __enter__(self) -> StreamIndicator:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def start(self) -> None:
        """Start the stream indicator if capabilities allow."""
        if self.caps.enabled and not self._started:
            try:
                self._status = self.console.status(
                    "[aegis.muted]▍ streaming...[/aegis.muted]",
                    spinner="dots",
                )
                self._status.start()
                self._started = True
            except Exception:
                self._status = None

    def update(self, chunks: int = 1, elapsed: float = 0.0) -> None:
        """Update the indicator with current chunk count and elapsed time. Never sleeps."""
        if not self.caps.enabled:
            return
        if not self._started:
            self.start()
        if self._status is not None:
            rate = (chunks / elapsed) if elapsed > 0.05 else 0.0
            msg = f"[aegis.muted]▍ streaming · {rate:.1f} tok/s[/aegis.muted]"
            try:
                self._status.update(msg)
            except Exception:
                pass

    def stop(self) -> None:
        """Stop and clear the stream indicator."""
        if self._status is not None:
            try:
                self._status.stop()
            except Exception:
                pass
            finally:
                self._status = None
        self._started = False


def completion(
    console: Console,
    caps: AnimatedCaps,
    elapsed: float,
    chunks: int,
) -> None:
    """Print subtle completion line when animations are enabled."""
    if not caps.enabled:
        return
    rate = (chunks / elapsed) if elapsed > 0.001 else 0.0
    console.print(
        f"[aegis.muted]▍ done · {chunks} chunks in {elapsed:.2f}s ({rate:.1f} tok/s)[/aegis.muted]"
    )
